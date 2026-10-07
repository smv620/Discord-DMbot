import type { Transform } from "node:stream";
import {
  EndBehaviorType,
  VoiceConnectionStatus,
  entersState,
  joinVoiceChannel,
  type AudioReceiveStream,
  type DiscordGatewayAdapterCreator,
  type JoinVoiceChannelOptions,
  type CreateVoiceConnectionOptions,
  type VoiceConnection,
} from "@discordjs/voice";
import prism from "prism-media";
import { Downsampler } from "./audio.js";
import type { Allowlist } from "./consent.js";
import type { CoreLink } from "./coreLink.js";
import type { Logger } from "./log.js";
import { SpeakerStates } from "./speakerStates.js";
import { UtteranceTracker, isSilenceFrame } from "./health.js";
import { encodeAudioFrame } from "./protocol.js";

/** End a speaker's stream after this much silence; one stream = one utterance. */
const SILENCE_END_MS = 800;

/** Someone Discord can't find is denied without asking again for this long. */
export const UNKNOWN_RETRY_MS = 30_000;

/**
 * Asks Discord whether a user is a bot: true or false, or undefined if they can't be
 * found (treated as a bot: deny by default). Only used for someone the session never
 * saw in the channel, because packets that arrive while it runs are lost.
 */
export type BotLookup = (userId: string) => Promise<boolean | undefined>;

/** Bot status if discord.js already has the member cached, without waiting. */
export type BotPeek = (userId: string) => boolean | undefined;

interface SpeakerPipeline {
  stream: AudioReceiveStream;
  decoder: Transform;
  downsampler: Downsampler;
  tracker: UtteranceTracker;
}

export interface TableSessionOptions {
  guildId: string;
  channelId: string;
  adapterCreator: DiscordGatewayAdapterCreator;
  allowlist: Allowlist;
  link: Pick<CoreLink, "send" | "sendAudio" | "droppedAudioFrames">;
  peekBot: BotPeek;
  lookUpBot: BotLookup;
  /** Log per-utterance audio health (user IDs and counts only). */
  debugAudio?: boolean;
  log: Logger;
  /** Called once when the session ends for any reason. */
  onClosed?: () => void;
  /** Tests only: replace the Discord voice connection and the Opus decoder. */
  connect?: (config: JoinVoiceChannelOptions & CreateVoiceConnectionOptions) => VoiceConnection;
  createDecoder?: () => Transform;
}

/**
 * One live connection to the table voice channel. Subscribes only to allowlisted,
 * non-bot speakers, decodes their Opus audio, converts it to 16 kHz mono PCM and
 * streams it to core. Audio from anyone else is never subscribed to or decoded.
 */
/** How long to wait for the voice connection, encryption handshake included. */
export const READY_TIMEOUT_MS = 20_000;

export class TableSession {
  readonly guildId: string;
  readonly channelId: string;
  private readonly connection: VoiceConnection;
  private readonly speakers = new Map<string, SpeakerPipeline>();
  private readonly pending = new Set<string>();
  /**
   * Bot or person, for everyone seen in the channel. Bot status never changes, so
   * entries stay until the session ends (a few dozen people at most).
   */
  private readonly bots = new Map<string, boolean>();
  /** People Discord couldn't find, and when to ask again. */
  private readonly unknownUntil = new Map<string, number>();
  private readonly states = new SpeakerStates();
  private destroyed = false;

  constructor(private readonly options: TableSessionOptions) {
    this.guildId = options.guildId;
    this.channelId = options.channelId;
    this.connection = (options.connect ?? joinVoiceChannel)({
      guildId: options.guildId,
      channelId: options.channelId,
      adapterCreator: options.adapterCreator,
      selfDeaf: false, // must hear to transcribe
      selfMute: true, // the bot never speaks in voice
    });

    this.connection.receiver.speaking.on("start", (userId) => {
      this.onSpeakingStart(userId);
    });

    this.connection.on(VoiceConnectionStatus.Disconnected, () => {
      void this.onDisconnected();
    });
  }

  /** Wait until the voice connection is ready (DAVE handshake included). */
  async ready(timeoutMs = READY_TIMEOUT_MS): Promise<void> {
    await entersState(this.connection, VoiceConnectionStatus.Ready, timeoutMs);
  }

  /**
   * Remember whether someone in the channel is a bot, so their speech can be
   * subscribed to the moment it starts (see onSpeakingStart).
   */
  noteMember(userId: string, isBot: boolean): void {
    if (this.destroyed) return;
    this.bots.set(userId, isBot);
    this.unknownUntil.delete(userId);
  }

  /** Whether this session already knows if the user is a bot. */
  knows(userId: string): boolean {
    return this.bots.has(userId);
  }

  /** Stop capturing these users immediately (consent revoked, or a pause). */
  dropSpeakers(userIds: readonly string[], reason = "opted out"): void {
    for (const userId of userIds) {
      // Stop first, then log. Only log people this session has heard: the consent
      // list holds everyone in the server who opted in, not just this channel.
      const heard = this.speakers.has(userId) || this.states.has(userId);
      this.endSpeaker(userId, false);
      if (heard) this.noteState(userId, false, reason);
    }
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const userId of [...this.speakers.keys()]) this.endSpeaker(userId, false);
    this.bots.clear();
    this.unknownUntil.clear();
    if (this.connection.state.status !== VoiceConnectionStatus.Destroyed) {
      this.connection.destroy();
    }
    this.options.onClosed?.();
  }

  /**
   * The receiver emits "start" for a speaker's first packet and then routes that same
   * packet to their subscription, if any. Deciding and subscribing synchronously here
   * keeps the first packet and everything after it; any await loses what arrives
   * meanwhile. So bot status comes from the members noted in advance (or discord.js's
   * member cache), and Discord is only asked about someone never seen in the channel.
   */
  private onSpeakingStart(userId: string): void {
    if (this.destroyed || this.speakers.has(userId) || this.pending.has(userId)) return;
    let isBot = this.bots.get(userId);
    if (isBot === undefined) {
      isBot = this.options.peekBot(userId);
      if (isBot !== undefined) this.bots.set(userId, isBot);
    }
    if (isBot !== undefined) {
      this.admit(userId, isBot);
      return;
    }
    if ((this.unknownUntil.get(userId) ?? 0) > Date.now()) {
      this.admit(userId, true); // couldn't be found just now: deny without asking again
      return;
    }
    void this.lookUpThenAdmit(userId);
  }

  private async lookUpThenAdmit(userId: string): Promise<void> {
    // Nobody who hasn't opted in is looked up when they speak, let alone captured.
    if (!this.options.allowlist.isAllowed(this.guildId, userId, false)) {
      this.noteState(userId, false, "not opted in, or a bot");
      return;
    }
    this.options.log.debug(`user ${userId} wasn't seen joining; looking them up`, { guildId: this.guildId });
    this.pending.add(userId);
    try {
      const isBot = await this.options.lookUpBot(userId).catch(() => undefined);
      if (this.destroyed) return;
      if (isBot === undefined) this.unknownUntil.set(userId, Date.now() + UNKNOWN_RETRY_MS);
      else this.bots.set(userId, isBot);
      this.admit(userId, isBot ?? true); // checks consent again: it may have changed
    } finally {
      this.pending.delete(userId);
    }
  }

  private admit(userId: string, isBot: boolean): void {
    if (!this.options.allowlist.isAllowed(this.guildId, userId, isBot)) {
      this.noteState(userId, false, "not opted in, or a bot");
      return;
    }
    this.noteState(userId, true);
    this.subscribe(userId);
  }

  private noteState(userId: string, capturing: boolean, reason?: string): void {
    const line = this.states.note(userId, capturing, reason);
    if (line) this.options.log.info(line, { guildId: this.guildId });
  }

  private subscribe(userId: string): void {
    const { link } = this.options;
    const stream = this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_END_MS },
    });
    const decoder =
      this.options.createDecoder?.() ?? new prism.opus.Decoder({ rate: 48_000, channels: 2, frameSize: 960 });
    const pipeline: SpeakerPipeline = {
      stream,
      decoder,
      downsampler: new Downsampler(),
      tracker: new UtteranceTracker(),
    };
    this.speakers.set(userId, pipeline);

    link.send({ type: "speaking", guildId: this.guildId, userId, event: "start", timestampMs: Date.now() });

    // Health is measured on the Opus packets as they arrive, so silence frames
    // (which mark a pause) can be told apart from lost packets. Times are delivery
    // times; this listener runs before the piped decoder sees each packet.
    stream.on("data", (packet: Buffer) => {
      pipeline.tracker.packet(Date.now(), isSilenceFrame(packet));
    });

    decoder.on("data", (pcm48k: Buffer) => {
      const now = Date.now();
      const pcm16k = pipeline.downsampler.push(pcm48k);
      if (pcm16k.length > 0) link.sendAudio(encodeAudioFrame(this.guildId, userId, now, pcm16k));
    });

    decoder.on("error", (err: Error) => {
      this.options.log.warn(`decode error for user ${userId}: ${err.message}`, { guildId: this.guildId });
      this.endSpeaker(userId, true);
    });
    stream.on("error", (err: Error) => {
      this.options.log.warn(`receive error for user ${userId}: ${err.message}`, { guildId: this.guildId });
      this.endSpeaker(userId, true);
    });
    stream.on("end", () => this.endSpeaker(userId, true));

    stream.pipe(decoder);
  }

  private endSpeaker(userId: string, report: boolean): void {
    const pipeline = this.speakers.get(userId);
    if (!pipeline) return;
    this.speakers.delete(userId);

    pipeline.stream.unpipe(pipeline.decoder);
    pipeline.stream.destroy();
    pipeline.decoder.destroy();

    const { link } = this.options;
    link.send({ type: "speaking", guildId: this.guildId, userId, event: "end", timestampMs: Date.now() });
    const health = pipeline.tracker.finish();
    if (report && health) {
      // Send only the protocol fields; pauses/pausedMs are local debug info.
      const { framesReceived, framesExpected } = health;
      link.send({ type: "health", guildId: this.guildId, userId, framesReceived, framesExpected });
      if (this.options.debugAudio) {
        this.options.log.info(
          `audio user=${userId} received=${framesReceived} expected=${framesExpected} ` +
            `pauses=${health.pauses} paused_ms=${health.pausedMs} ` +
            `link_dropped_total=${link.droppedAudioFrames}`,
          { guildId: this.guildId },
        );
      }
    }
  }

  /** Standard @discordjs/voice recovery: give it 5 s to reconnect, else give up. */
  private async onDisconnected(): Promise<void> {
    try {
      await Promise.race([
        entersState(this.connection, VoiceConnectionStatus.Signalling, 5_000),
        entersState(this.connection, VoiceConnectionStatus.Connecting, 5_000),
      ]);
    } catch {
      this.options.log.warn("lost the voice connection and couldn't reconnect", { guildId: this.guildId });
      this.options.link.send({
        type: "status",
        state: "left",
        guildId: this.guildId,
        detail: "Lost the voice connection and could not reconnect.",
      });
      this.destroy();
    }
  }
}

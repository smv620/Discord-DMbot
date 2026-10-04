import {
  EndBehaviorType,
  VoiceConnectionStatus,
  entersState,
  joinVoiceChannel,
  type AudioReceiveStream,
  type DiscordGatewayAdapterCreator,
  type VoiceConnection,
} from "@discordjs/voice";
import prism from "prism-media";
import { Downsampler } from "./audio.js";
import type { Allowlist } from "./consent.js";
import type { CoreLink } from "./coreLink.js";
import type { Logger } from "./log.js";
import { UtteranceTracker, isSilenceFrame } from "./health.js";
import { encodeAudioFrame } from "./protocol.js";

/** End a speaker's stream after this much silence; one stream = one utterance. */
const SILENCE_END_MS = 800;

/** Returns true if the user is a bot OR cannot be identified (deny by default). */
export type BotCheck = (userId: string) => Promise<boolean>;

interface SpeakerPipeline {
  stream: AudioReceiveStream;
  decoder: prism.opus.Decoder;
  downsampler: Downsampler;
  tracker: UtteranceTracker;
}

export interface TableSessionOptions {
  guildId: string;
  channelId: string;
  adapterCreator: DiscordGatewayAdapterCreator;
  allowlist: Allowlist;
  link: CoreLink;
  isBotOrUnknown: BotCheck;
  /** Log per-utterance audio health (user IDs and counts only). */
  debugAudio?: boolean;
  log: Logger;
  /** Called once when the session ends for any reason. */
  onClosed?: () => void;
}

/**
 * One live connection to the table voice channel. Subscribes only to allowlisted,
 * non-bot speakers, decodes their Opus audio, converts it to 16 kHz mono PCM and
 * streams it to core. Audio from anyone else is never subscribed to or decoded.
 */
export class TableSession {
  readonly guildId: string;
  readonly channelId: string;
  private readonly connection: VoiceConnection;
  private readonly speakers = new Map<string, SpeakerPipeline>();
  private readonly pending = new Set<string>();
  private destroyed = false;

  constructor(private readonly options: TableSessionOptions) {
    this.guildId = options.guildId;
    this.channelId = options.channelId;
    this.connection = joinVoiceChannel({
      guildId: options.guildId,
      channelId: options.channelId,
      adapterCreator: options.adapterCreator,
      selfDeaf: false, // must hear to transcribe
      selfMute: true, // the bot never speaks in voice
    });

    this.connection.receiver.speaking.on("start", (userId) => {
      void this.onSpeakingStart(userId);
    });

    this.connection.on(VoiceConnectionStatus.Disconnected, () => {
      void this.onDisconnected();
    });
  }

  /** Wait until the voice connection is ready (DAVE handshake included). */
  async ready(timeoutMs = 20_000): Promise<void> {
    await entersState(this.connection, VoiceConnectionStatus.Ready, timeoutMs);
  }

  /** Stop capturing these users immediately (consent revoked). */
  dropSpeakers(userIds: readonly string[]): void {
    for (const userId of userIds) this.endSpeaker(userId, false);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const userId of [...this.speakers.keys()]) this.endSpeaker(userId, false);
    if (this.connection.state.status !== VoiceConnectionStatus.Destroyed) {
      this.connection.destroy();
    }
    this.options.onClosed?.();
  }

  private async onSpeakingStart(userId: string): Promise<void> {
    if (this.destroyed || this.speakers.has(userId) || this.pending.has(userId)) return;
    this.pending.add(userId);
    try {
      const isBot = await this.options.isBotOrUnknown(userId);
      if (this.destroyed || !this.options.allowlist.isAllowed(this.guildId, userId, isBot)) return;
      this.subscribe(userId);
    } finally {
      this.pending.delete(userId);
    }
  }

  private subscribe(userId: string): void {
    const { link } = this.options;
    const stream = this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_END_MS },
    });
    const decoder = new prism.opus.Decoder({ rate: 48_000, channels: 2, frameSize: 960 });
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

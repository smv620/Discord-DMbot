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
import { FRAME_MS, UtteranceTracker, isSilenceFrame } from "./health.js";
import { encodeAudioFrame } from "./protocol.js";

/** End a speaker's stream after this much silence; one stream = one utterance. */
const SILENCE_END_MS = 800;

/** Someone Discord can't find is denied without asking again for this long. */
export const UNKNOWN_RETRY_MS = 30_000;

/**
 * After a failure (a receive error, #631, or a stream ended while they still send, #645) a
 * speaker is listened to again, up to this many times a minute; then ears gives up on them
 * until they next start speaking, and tells core.
 */
export const RESUBSCRIBE_LIMIT = 5;
export const RESUBSCRIBE_WINDOW_MS = 60_000;
/**
 * The first try is at once; later ones wait this long. Once the library starts erroring it
 * errors on every failing packet until one decrypts, so tries 20 ms apart would all be
 * spent in a tenth of a second. The wait counts as lost.
 */
export const RESUBSCRIBE_DELAY_MS = 1_000;
/** Packets arriving but none heard for this many watchdog periods: core is told too. */
export const SILENT_PERIODS_BEFORE_WARNING = 3;

/**
 * @discordjs/voice drops packets it can't decrypt without a word, and its stream only ends
 * after a packet arrives, so a speaker whose packets all fail would be listened to forever
 * and heard as nothing (#631). If nothing arrives for this long, ears looks at whether
 * they are still sending (the library's speaking map sees packets before decryption): if
 * so, that time is lost audio; if not, their speech has ended.
 */
export const WATCHDOG_MS = 1_000;

/** Lines about failed decrypts come with every packet: at most one is logged this often. */
export const DECRYPT_LOG_EVERY_MS = 1_000;

/** The voice library's SpeakingMap lets a speaker go this long after their last packet. */
const SPEAKING_DELAY_MS = 100;

/**
 * Asks Discord whether a user is a bot: true or false, or undefined if they can't be
 * found (treated as a bot: deny by default). Only used for someone the session never
 * saw in the channel, because packets that arrive while it runs are lost.
 */
export type BotLookup = (userId: string) => Promise<boolean | undefined>;

/** Bot status if discord.js already has the member cached, without waiting. */
export type BotPeek = (userId: string) => boolean | undefined;

interface SpeakerPipeline {
  stream: AudioReceiveStream; // replaced when re-listening after a failure
  decoder: Transform;
  downsampler: Downsampler;
  tracker: UtteranceTracker;
  /** Up to when audio is accounted for: the last packet heard, or the last loss counted. */
  accounted: number;
  watchdog?: NodeJS.Timeout;
  retry?: NodeJS.Timeout; // a delayed re-listen
  warnedSilent: boolean; // logged "sending but nothing heard" for this pipeline
  silentPeriods: number; // watchdog periods in a row with packets arriving, none heard
}

export interface TableSessionOptions {
  guildId: string;
  channelId: string;
  adapterCreator: DiscordGatewayAdapterCreator;
  allowlist: Allowlist;
  link: Pick<CoreLink, "send" | "sendAudio" | "droppedAudioFrames">;
  peekBot: BotPeek;
  lookUpBot: BotLookup;
  /**
   * Log per-utterance audio health (user IDs and counts only), and turn on the voice
   * library's debug events, of which only its encryption (`[NW] [DAVE] `) lines are logged
   * (see onVoiceDebug).
   */
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
  /** When each speaker was last listened to again after a failure. */
  private readonly retries = new Map<string, number[]>();
  /** When core was last told a speaker's audio kept failing. */
  private readonly warnedAt = new Map<string, number>();
  private lastDecryptLog = -Infinity;
  private decryptLinesSkipped = 0;
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
      debug: options.debugAudio === true, // for the encryption handshake's lines (#631)
    });
    if (options.debugAudio) {
      this.connection.on("debug", (message: string) => {
        this.onVoiceDebug(message);
      });
    }

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
    this.retries.clear();
    this.warnedAt.clear();
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
    const pipeline: SpeakerPipeline = {
      stream: this.receiveStream(userId),
      decoder:
        this.options.createDecoder?.() ?? new prism.opus.Decoder({ rate: 48_000, channels: 2, frameSize: 960 }),
      downsampler: new Downsampler(),
      tracker: new UtteranceTracker(),
      accounted: Date.now(),
      warnedSilent: false,
      silentPeriods: 0,
    };
    this.speakers.set(userId, pipeline);

    link.send({ type: "speaking", guildId: this.guildId, userId, event: "start", timestampMs: Date.now() });

    pipeline.decoder.on("data", (pcm48k: Buffer) => {
      const now = Date.now();
      const pcm16k = pipeline.downsampler.push(pcm48k);
      if (pcm16k.length > 0) link.sendAudio(encodeAudioFrame(this.guildId, userId, now, pcm16k));
    });
    pipeline.decoder.on("error", (err: Error) => {
      this.options.log.warn(`decode error for user ${userId}: ${err.message}`, { guildId: this.guildId });
      this.endSpeaker(userId, true);
    });
    this.listen(userId, pipeline);
  }

  private receiveStream(userId: string): AudioReceiveStream {
    return this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_END_MS },
    });
  }

  /** Wire a speaker's (current) receive stream into their pipeline. */
  private listen(userId: string, pipeline: SpeakerPipeline): void {
    const { stream } = pipeline;
    const current = (): boolean => this.speakers.get(userId) === pipeline && pipeline.stream === stream;
    // Health is measured on the Opus packets as they arrive, so silence frames
    // (which mark a pause) can be told apart from lost packets. Times are delivery
    // times; this listener runs before the piped decoder sees each packet.
    stream.on("data", (packet: Buffer) => {
      if (!current()) return;
      const now = Date.now();
      pipeline.accounted = now;
      pipeline.silentPeriods = 0;
      pipeline.tracker.packet(now, isSilenceFrame(packet));
      this.arm(userId, pipeline);
    });
    stream.on("error", (err: Error) => {
      if (current()) this.onFailure(userId, pipeline, `receive error: ${err.message}`);
    });
    stream.on("end", () => {
      if (!current()) return;
      // The library ends a stream 800 ms after the last packet it could decrypt. If the
      // speaker is still sending, their packets are being dropped without an error (#645):
      // that's a failure, not the end of their speech.
      // Nothing heard for more than the speaking map's 100 ms either: a client that sends
      // silence frames through a pause is still heard, and ends normally.
      const unheard = Date.now() - pipeline.accounted > SPEAKING_DELAY_MS;
      if (unheard && this.connection.receiver.speaking.users.has(userId)) {
        this.onFailure(userId, pipeline, "the stream ended while they were still sending");
      } else {
        this.endSpeaker(userId, true);
      }
    });
    // Not ended with the stream: one decoder serves a speaker across re-listening.
    stream.pipe(pipeline.decoder, { end: false });
    this.arm(userId, pipeline);
  }

  /** (Re)start the speaker's watchdog (see WATCHDOG_MS). */
  private arm(userId: string, pipeline: SpeakerPipeline): void {
    clearTimeout(pipeline.watchdog);
    pipeline.watchdog = setTimeout(() => {
      this.onQuiet(userId, pipeline);
    }, WATCHDOG_MS);
    pipeline.watchdog.unref?.();
  }

  /**
   * Lost frames for the time since audio was last accounted for, at least one. After a
   * pause (a silence run) only one: the quiet was the pause, not loss.
   */
  private loseSince(pipeline: SpeakerPipeline, now: number): void {
    const gap = Math.round((now - pipeline.accounted) / FRAME_MS);
    pipeline.tracker.lost(now, pipeline.tracker.paused() ? 1 : Math.max(1, gap));
    pipeline.accounted = now;
  }

  /** Tell core a speaker's audio keeps failing: at most once a minute per speaker. */
  private warnCore(userId: string, now: number, why: string): void {
    const warned = this.warnedAt.get(userId);
    if (warned !== undefined && now - warned < RESUBSCRIBE_WINDOW_MS) return;
    this.warnedAt.set(userId, now);
    this.options.log.warn(`user ${userId}: ${why}; telling core`, { guildId: this.guildId });
    this.options.link.send({
      type: "status",
      state: "warning",
      guildId: this.guildId,
      userId,
      detail: "Their audio kept failing.",
    });
  }

  private onQuiet(userId: string, pipeline: SpeakerPipeline): void {
    if (this.destroyed || this.speakers.get(userId) !== pipeline) return;
    if (!this.connection.receiver.speaking.users.has(userId)) {
      this.endSpeaker(userId, true); // nothing arriving at all: their speech is over
      return;
    }
    // Packets arrive but none get through: the library is dropping them (#631).
    if (!pipeline.warnedSilent) {
      pipeline.warnedSilent = true;
      this.options.log.warn(`user ${userId} is sending audio but none of it can be heard`, {
        guildId: this.guildId,
      });
    }
    const now = Date.now();
    this.loseSince(pipeline, now);
    pipeline.silentPeriods++;
    if (pipeline.silentPeriods === SILENT_PERIODS_BEFORE_WARNING) {
      this.warnCore(userId, now, "sending audio but none heard for a while");
    }
    this.arm(userId, pipeline);
  }

  /**
   * A speaker's stream failed: the voice library errored it because their packets
   * wouldn't decrypt (#631), or ended it while they were still sending (#645). They are still sending, so listen again at
   * once rather than wait for a pause and a new "start", and count the time since the
   * last packet heard as lost. If it keeps happening, stop until they next start speaking
   * and tell core (once a minute), so the DM hears of it.
   */
  private onFailure(userId: string, pipeline: SpeakerPipeline, why: string): void {
    const now = Date.now();
    this.loseSince(pipeline, now);
    const tries = this.retries.get(userId)?.filter((at) => now - at < RESUBSCRIBE_WINDOW_MS) ?? [];
    if (tries.length >= RESUBSCRIBE_LIMIT) {
      this.warnCore(
        userId,
        now,
        `${RESUBSCRIBE_LIMIT} failures in a minute (${why}); not listening again until ` +
          "they next start speaking",
      );
      this.endSpeaker(userId, true);
      return;
    }
    const delay = tries.length === 0 ? 0 : RESUBSCRIBE_DELAY_MS;
    tries.push(now);
    this.retries.set(userId, tries);
    this.options.log.warn(
      `user ${userId}: ${why}; listening again (${tries.length} of ${RESUBSCRIBE_LIMIT} ` +
        "this minute)",
      { guildId: this.guildId },
    );
    const old = pipeline.stream;
    old.unpipe(pipeline.decoder);
    clearTimeout(pipeline.watchdog); // the re-listen accounts for the wait
    // The receiver forgets the old stream on its "close" (which always follows "error" or
    // "end", though `closed` may already be true here); only then can it give a new one.
    old.once("close", () => {
      if (delay === 0) {
        this.listenAgain(userId, pipeline);
        return;
      }
      pipeline.retry = setTimeout(() => this.listenAgain(userId, pipeline), delay);
      pipeline.retry.unref?.();
    });
  }

  private listenAgain(userId: string, pipeline: SpeakerPipeline): void {
    if (this.destroyed || this.speakers.get(userId) !== pipeline) return; // stopped meanwhile
    // Consent is checked again, as for every subscription.
    if (!this.options.allowlist.isAllowed(this.guildId, userId, this.bots.get(userId) ?? true)) {
      this.endSpeaker(userId, true);
      return;
    }
    pipeline.stream = this.receiveStream(userId);
    this.listen(userId, pipeline);
  }

  /**
   * The voice library's encryption (DAVE) debug lines, with DMBOT_DEBUG_AUDIO=1 (#631):
   * transitions, versions, failure counts. Only those: its other lines (the websocket's)
   * can hold the session's credentials. Failed-decrypt lines come per packet, so at most
   * one a second is logged, with how many were skipped.
   */
  private onVoiceDebug(message: string): void {
    const prefix = "[NW] [DAVE] ";
    if (!message.startsWith(prefix)) return;
    const text = message.slice(prefix.length);
    if (text.startsWith("Failed to decrypt a packet")) {
      const now = Date.now();
      if (now - this.lastDecryptLog < DECRYPT_LOG_EVERY_MS) {
        this.decryptLinesSkipped++;
        return;
      }
      const skipped = this.decryptLinesSkipped;
      this.lastDecryptLog = now;
      this.decryptLinesSkipped = 0;
      this.options.log.info(`dave: ${text}${skipped ? ` (and ${skipped} more like it)` : ""}`, {
        guildId: this.guildId,
      });
      return;
    }
    this.options.log.info(`dave: ${text}`, { guildId: this.guildId });
  }

  private endSpeaker(userId: string, report: boolean): void {
    const pipeline = this.speakers.get(userId);
    if (!pipeline) return;
    this.speakers.delete(userId);
    clearTimeout(pipeline.watchdog);
    clearTimeout(pipeline.retry);

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

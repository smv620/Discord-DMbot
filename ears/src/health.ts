/**
 * Audio health tracking. Discord sends one Opus packet per 20 ms while a user speaks.
 * Comparing packets received with the packets expected for the utterance's duration
 * reveals silent packet loss (a known risk with DAVE decryption), so the DM can see
 * whether capture is trustworthy.
 *
 * Clients pause transmission when the speaker pauses: they send five Opus silence
 * frames, then nothing until speech resumes. A gap right after a run of silence frames
 * is therefore a pause, not loss, and is left out of the expected count (issue #36).
 * Gaps without that run still count as loss.
 *
 * Packets the receiver drops on a decrypt failure never arrive, so the gap they leave
 * looks like nothing. When the receive stream errors (#631), the caller says how many were
 * lost (`lost`), and the time until the next packet counts as lost too.
 *
 * Known limit: a speaker whose client sends no silence frames, and whose packets are
 * failing, has a short pause counted as lost (the receiver can't tell the two apart).
 *
 * Known limit: packets lost right after a pause are indistinguishable from the pause
 * itself (DAVE passes silence frames through undecrypted, so a decrypt failure on the
 * resumed speech looks exactly like this). At most ~800 ms per pause can hide this way,
 * since lost packets don't keep the receive stream alive. Exact detection needs RTP
 * sequence numbers or DAVE decrypt-failure counts (#43).
 */
export const FRAME_MS = 20;

/** Discord's Opus silence frame. */
export const SILENCE_FRAME = Buffer.from([0xf8, 0xff, 0xfe]);

/** A gap longer than this after a silence run is a pause (allows for network jitter). */
export const PAUSE_MIN_MS = 2 * FRAME_MS;

/** Silence frames in a row before a gap counts as a pause (clients send 5). */
export const PAUSE_MIN_SILENCE_FRAMES = 3;

export function isSilenceFrame(packet: Buffer): boolean {
  return packet.equals(SILENCE_FRAME);
}

export interface UtteranceHealth {
  framesReceived: number;
  framesExpected: number;
}

export interface UtteranceReport extends UtteranceHealth {
  /** Pauses left out of the expected count, and their total length. For debugging. */
  pauses: number;
  pausedMs: number;
}

/** Frames expected between two packet arrival times, inclusive of both packets. */
function span(startMs: number, endMs: number): number {
  return Math.round((endMs - startMs) / FRAME_MS) + 1;
}

export class UtteranceTracker {
  private segmentStart: number | null = null;
  private lastAt: number | null = null;
  private silenceRun = 0;
  private received = 0;
  private expected = 0;
  private pauses = 0;
  private pausedMs = 0;
  private lostAt: number | null = null; // the last receive error, until audio resumes

  packet(nowMs: number, silence: boolean): void {
    if (this.lostAt !== null) {
      // Audio resumed after a receive error: the time since it was lost too.
      this.expected += Math.max(0, Math.round((nowMs - this.lostAt) / FRAME_MS) - 1);
      this.lostAt = null;
    }
    if (this.segmentStart === null || this.lastAt === null) {
      this.segmentStart = nowMs;
    } else {
      const gap = nowMs - this.lastAt;
      if (this.silenceRun >= PAUSE_MIN_SILENCE_FRAMES && gap > PAUSE_MIN_MS) {
        // The speaker paused: close the segment and don't expect frames for the gap.
        this.expected += span(this.segmentStart, this.lastAt);
        this.segmentStart = nowMs;
        this.pauses++;
        this.pausedMs += gap - FRAME_MS;
      }
    }
    this.lastAt = nowMs;
    this.silenceRun = silence ? this.silenceRun + 1 : 0;
    this.received++;
  }

  /** The last packets were a run of silence frames: the speaker paused. */
  paused(): boolean {
    return this.silenceRun >= PAUSE_MIN_SILENCE_FRAMES;
  }

  /**
   * `frames` packets were lost at `nowMs` (the receive stream errored, #631). Closes the
   * current stretch of audio, so the lost packets aren't also counted in its span.
   */
  lost(nowMs: number, frames: number): void {
    if (this.segmentStart !== null && this.lastAt !== null) {
      this.expected += span(this.segmentStart, this.lastAt);
    }
    this.segmentStart = null;
    this.lastAt = null;
    this.silenceRun = 0;
    this.expected += Math.max(0, frames);
    this.lostAt = nowMs;
  }

  /** Finish the utterance and return its health, or null if nothing arrived or was lost. */
  finish(): UtteranceReport | null {
    const open = this.segmentStart !== null && this.lastAt !== null;
    if (!open && this.expected === 0) {
      this.lostAt = null;
      return null;
    }
    const segment =
      this.segmentStart !== null && this.lastAt !== null ? span(this.segmentStart, this.lastAt) : 0;
    const framesExpected = Math.max(1, this.expected + segment);
    const report: UtteranceReport = {
      // Jitter bursts can deliver more packets than a span predicts; clamp per utterance
      // so one utterance's surplus can't mask another's loss in core's totals.
      framesReceived: Math.min(this.received, framesExpected),
      framesExpected,
      pauses: this.pauses,
      pausedMs: this.pausedMs,
    };
    this.segmentStart = null;
    this.lastAt = null;
    this.silenceRun = 0;
    this.received = 0;
    this.expected = 0;
    this.pauses = 0;
    this.pausedMs = 0;
    this.lostAt = null;
    return report;
  }
}

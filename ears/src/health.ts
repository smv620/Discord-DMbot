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

  packet(nowMs: number, silence: boolean): void {
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

  /** Finish the utterance and return its health, or null if no audio arrived. */
  finish(): UtteranceReport | null {
    if (this.segmentStart === null || this.lastAt === null) return null;
    const framesExpected = Math.max(1, this.expected + span(this.segmentStart, this.lastAt));
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
    return report;
  }
}

/**
 * Audio health tracking. Discord sends one Opus packet per 20 ms while a user speaks.
 * Comparing packets received with the packets expected for the utterance's duration
 * reveals silent packet loss (a known risk with DAVE decryption), so the DM can see
 * whether capture is trustworthy.
 *
 * Clients pause transmission when the speaker pauses: they send a few Opus silence
 * frames, then nothing until speech resumes. A gap right after a silence frame is
 * therefore a pause, not loss, and is left out of the expected count (issue #36).
 * Gaps without a preceding silence frame still count as loss.
 */
export const FRAME_MS = 20;

/** Discord's Opus silence frame. */
export const SILENCE_FRAME = Buffer.from([0xf8, 0xff, 0xfe]);

/** A gap longer than this after a silence frame is a pause (allows for network jitter). */
export const PAUSE_MIN_MS = 2 * FRAME_MS;

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
  private lastWasSilence = false;
  private received = 0;
  private expected = 0;
  private pauses = 0;
  private pausedMs = 0;

  packet(nowMs: number, silence: boolean): void {
    if (this.segmentStart === null || this.lastAt === null) {
      this.segmentStart = nowMs;
    } else {
      const gap = nowMs - this.lastAt;
      if (this.lastWasSilence && gap > PAUSE_MIN_MS) {
        // The speaker paused: close the segment and don't expect frames for the gap.
        this.expected += span(this.segmentStart, this.lastAt);
        this.segmentStart = nowMs;
        this.pauses++;
        this.pausedMs += gap - FRAME_MS;
      }
    }
    this.lastAt = nowMs;
    this.lastWasSilence = silence;
    this.received++;
  }

  /** Finish the utterance and return its health, or null if no audio arrived. */
  finish(): UtteranceReport | null {
    if (this.segmentStart === null || this.lastAt === null) return null;
    const report: UtteranceReport = {
      framesReceived: this.received,
      framesExpected: Math.max(1, this.expected + span(this.segmentStart, this.lastAt)),
      pauses: this.pauses,
      pausedMs: this.pausedMs,
    };
    this.segmentStart = null;
    this.lastAt = null;
    this.lastWasSilence = false;
    this.received = 0;
    this.expected = 0;
    this.pauses = 0;
    this.pausedMs = 0;
    return report;
  }
}

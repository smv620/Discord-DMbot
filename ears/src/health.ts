/**
 * Audio health tracking. Discord sends one Opus frame per 20 ms while a user speaks.
 * Comparing frames received with the frames expected for the utterance's duration
 * reveals silent packet loss (a known risk with DAVE decryption), so the DM can see
 * whether capture is trustworthy.
 */
export const FRAME_MS = 20;

export interface UtteranceHealth {
  framesReceived: number;
  framesExpected: number;
}

export class UtteranceTracker {
  private startedAt: number | null = null;
  private lastFrameAt: number | null = null;
  private frames = 0;

  frame(nowMs: number): void {
    if (this.startedAt === null) this.startedAt = nowMs;
    this.lastFrameAt = nowMs;
    this.frames++;
  }

  /** Finish the utterance and return its health, or null if no audio arrived. */
  finish(): UtteranceHealth | null {
    if (this.startedAt === null || this.lastFrameAt === null) return null;
    // The first frame covers [start, start + 20 ms], so add one frame.
    const expected = Math.max(1, Math.round((this.lastFrameAt - this.startedAt) / FRAME_MS) + 1);
    const result = { framesReceived: this.frames, framesExpected: expected };
    this.startedAt = null;
    this.lastFrameAt = null;
    this.frames = 0;
    return result;
  }
}

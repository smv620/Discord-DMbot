/**
 * When to reconnect to core. Pure logic, so it can be tested without sockets.
 *
 * - Exponential backoff from MIN to MAX.
 * - The backoff only resets after a connection has stayed up for STABLE_MS. core accepts
 *   or refuses the hello right after the socket opens, so "opened" alone isn't success.
 * - If core refused this ears (close code 1008: wrong secret, version, or shards), wait
 *   the maximum: retrying fast can't help until someone fixes the settings.
 */
export const MIN_BACKOFF_MS = 500;
export const MAX_BACKOFF_MS = 15_000;
export const STABLE_MS = 10_000;
export const REJECTED_CLOSE_CODE = 1008;

export interface CloseDecision {
  delayMs: number;
  rejected: boolean;
}

export class ReconnectPolicy {
  private backoffMs = MIN_BACKOFF_MS;
  private openedAt: number | null = null;

  opened(nowMs: number): void {
    this.openedAt = nowMs;
  }

  closed(nowMs: number, code: number): CloseDecision {
    const wasStable = this.openedAt !== null && nowMs - this.openedAt >= STABLE_MS;
    this.openedAt = null;
    if (code === REJECTED_CLOSE_CODE) {
      this.backoffMs = MAX_BACKOFF_MS;
      return { delayMs: MAX_BACKOFF_MS, rejected: true };
    }
    if (wasStable) this.backoffMs = MIN_BACKOFF_MS;
    const delayMs = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
    return { delayMs, rejected: false };
  }
}

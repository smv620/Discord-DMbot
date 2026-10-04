/**
 * Remembers, per voice session, whether each speaker is being captured, so the log
 * shows only changes ("capturing user 1", "not capturing user 1: opted out") instead
 * of a line per utterance. Lets a live test be judged from the terminal (#37).
 * User IDs only — never names or audio.
 */
export class SpeakerStates {
  private readonly capturing = new Map<string, boolean>();

  /** The log line if this user's state changed, else null. */
  note(userId: string, capturing: boolean, reason = ""): string | null {
    if (this.capturing.get(userId) === capturing) return null;
    this.capturing.set(userId, capturing);
    if (capturing) return `capturing user ${userId}`;
    return `not capturing user ${userId}${reason ? `: ${reason}` : ""}`;
  }
}

/**
 * After the link to core comes back (core restarted, or the link dropped), core re-sends
 * what it wants: allowlist + join for every session it's running. Any voice session core
 * doesn't mention within CONFIRM_MS is one it no longer wants (for example the DM ran
 * /dmbot stop while ears was cut off), so ears leaves it rather than staying in the
 * channel. Pure logic, so it can be tested without Discord.
 */
export const CONFIRM_MS = 60_000;

export class SessionAudit {
  private pending = new Set<string>();

  /** The link came back: every current session must be confirmed again. */
  begin(guildIds: Iterable<string>): void {
    this.pending = new Set(guildIds);
  }

  /** core mentioned this server (join, allowlist or leave). */
  confirm(guildId: string): void {
    this.pending.delete(guildId);
  }

  /** Servers core never mentioned since `begin`. Clears the list. */
  takeUnconfirmed(): string[] {
    const out = [...this.pending];
    this.pending.clear();
    return out;
  }
}

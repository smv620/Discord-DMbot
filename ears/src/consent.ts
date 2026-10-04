/**
 * Capture allowlist. Core is the source of truth for consent and pushes the complete
 * list per guild; ears enforces it before any audio is decoded. Default is deny:
 * a guild with no list captures nobody, and bots are never captured.
 */
export class Allowlist {
  private readonly byGuild = new Map<string, ReadonlySet<string>>();

  /** Replace the allowlist for a guild. Returns users who were removed. */
  set(guildId: string, userIds: readonly string[]): string[] {
    const next = new Set(userIds);
    const previous = this.byGuild.get(guildId) ?? new Set<string>();
    this.byGuild.set(guildId, next);
    return [...previous].filter((id) => !next.has(id));
  }

  clear(guildId: string): void {
    this.byGuild.delete(guildId);
  }

  isAllowed(guildId: string, userId: string, isBot: boolean): boolean {
    if (isBot) return false;
    return this.byGuild.get(guildId)?.has(userId) ?? false;
  }
}

import type { Allowlist } from "./consent.js";
import { UNKNOWN_RETRY_MS, type BotLookup } from "./voice.js";

/** The parts of a discord.js VoiceState needed to tell a bot from a person. */
export interface VoiceMember {
  id: string;
  channelId: string | null;
  member: { user: { bot: boolean } } | null;
}

/** Somewhere to remember bot status (a TableSession). */
export interface MemberNotes {
  readonly channelId: string;
  noteMember(userId: string, isBot: boolean): void;
  knows(userId: string): boolean;
}

/**
 * Per session, people looked up here and when they may be asked about again: never
 * while a lookup runs, and only after UNKNOWN_RETRY_MS if Discord couldn't find them,
 * so a mute or deafen doesn't ask again each time. Kept apart from TableSession's own
 * retry time on purpose: someone who speaks is looked up at once, so their first
 * words are kept. Relies on lookUpBot always settling (discord.js times requests out).
 */
const askedUntil = new WeakMap<MemberNotes, Map<string, number>>();

/**
 * Note whether each person in the session's channel is a bot, before they speak, so
 * capture can start on their first packet. Someone discord.js hasn't cached is looked
 * up in the background, but only if they've opted in and aren't known yet (voice
 * states change on every mute or deafen); anyone who can't be found stays unknown, is
 * looked up here again after a while, and is looked up again at once if they speak.
 */
export function noteVoiceMembers(
  session: MemberNotes,
  states: Iterable<VoiceMember>,
  optedIn: (userId: string) => boolean,
  lookUpBot: BotLookup,
): void {
  for (const state of states) {
    if (state.channelId !== session.channelId) continue;
    if (state.member) {
      session.noteMember(state.id, state.member.user.bot);
      continue;
    }
    const userId = state.id;
    if (session.knows(userId) || !optedIn(userId)) continue;
    let asked = askedUntil.get(session);
    if (!asked) {
      asked = new Map();
      askedUntil.set(session, asked);
    }
    if ((asked.get(userId) ?? 0) > Date.now()) continue;
    asked.set(userId, Infinity);
    const found = (isBot: boolean | undefined): void => {
      if (isBot === undefined) {
        asked.set(userId, Date.now() + UNKNOWN_RETRY_MS); // looked up at once if they speak
        return;
      }
      asked.delete(userId);
      session.noteMember(userId, isBot);
    };
    void lookUpBot(userId).then(found, () => found(undefined));
  }
}

/** A table session as the consent list sees it. */
export interface ConsentTarget extends MemberNotes {
  dropSpeakers(userIds: readonly string[]): void;
}

/**
 * Core sent a server's new consent list: replace it, stop capturing anyone removed at
 * once, then look up anyone in the channel who just opted in and isn't known, so their
 * first words are kept. `voiceStates` is undefined if ears doesn't have the server
 * cached. Returns who was removed.
 */
export function applyConsentList(
  allowlist: Allowlist,
  guildId: string,
  userIds: readonly string[],
  session: ConsentTarget | undefined,
  voiceStates: Iterable<VoiceMember> | undefined,
  lookUpBot: BotLookup,
): string[] {
  const removed = allowlist.set(guildId, userIds);
  if (!session) return removed;
  session.dropSpeakers(removed);
  if (voiceStates) {
    noteVoiceMembers(session, voiceStates, (userId) => allowlist.isAllowed(guildId, userId, false), lookUpBot);
  }
  return removed;
}

import type { Allowlist } from "./consent.js";
import type { BotLookup } from "./voice.js";

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

/** Lookups still running, per session, so a second note doesn't ask Discord again. */
const inFlight = new WeakMap<MemberNotes, Set<string>>();

/**
 * Note whether each person in the session's channel is a bot, before they speak, so
 * capture can start on their first packet. Someone discord.js hasn't cached is looked
 * up in the background, but only if they've opted in and aren't known yet (voice
 * states change on every mute or deafen); anyone who can't be found stays unknown and
 * is looked up again if they speak.
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
    let running = inFlight.get(session);
    if (!running) inFlight.set(session, (running = new Set()));
    if (running.has(userId)) continue;
    running.add(userId);
    void lookUpBot(userId)
      .then(
        (isBot) => {
          if (isBot !== undefined) session.noteMember(userId, isBot);
        },
        () => undefined, // stays unknown: looked up again when they speak
      )
      .finally(() => running.delete(userId));
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

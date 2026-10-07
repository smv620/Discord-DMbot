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
}

/**
 * Note whether each person in the session's channel is a bot, before they speak, so
 * capture can start on their first packet. Anyone discord.js hasn't cached is looked
 * up in the background; anyone who can't be found stays unknown and is looked up
 * again if they speak.
 */
export function noteVoiceMembers(
  session: MemberNotes,
  states: Iterable<VoiceMember>,
  lookUpBot: BotLookup,
): void {
  for (const state of states) {
    if (state.channelId !== session.channelId) continue;
    if (state.member) {
      session.noteMember(state.id, state.member.user.bot);
      continue;
    }
    const userId = state.id;
    void lookUpBot(userId).then(
      (isBot) => {
        if (isBot !== undefined) session.noteMember(userId, isBot);
      },
      () => undefined, // stays unknown: looked up again when they speak
    );
  }
}

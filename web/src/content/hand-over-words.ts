// The words of the hand-over question (#4122), the same as Discord's card (core/src/dmbot/ui/hand_over_questions.py): the question
// first, what each answer does, then the line after an answer. Names are only ever text on the page. Plain words: the bot asks,
// the DM decides.
import type { HandOverQuestion } from "../account/api";

/** "the Frost Brand", but not "the the Frost Brand" or "the a Sword": the same rule as Discord's `the()`. */
export const the = (name: string): string => (/^(the|a|an)\s/i.test(name) ? name : `the ${name}`);

export const handOverWords = {
  heading: "Items that changed hands",
  loading: "Loading…",
  failed: "We couldn't load these. Reload the page to try again.",
  no: "No, not any more",
  both: "Yes, both have it",
  notNow: "Not now",
  notNowMeans: "Not now hides this until you reload the page.",
  undo: "↩ Undo that",
  saving: "Saving your answer",
  waiting: (n: number): string => `${n} waiting`,
  // Said after an answer (Discord's done_text, both_text and undone_text).
  done: (older: string, newer: string, item: string): string => `Done. ${newer} has ${the(item)} now. ${older} used to have it.`,
  bothKept: (older: string, newer: string, item: string): string =>
    `OK. ${older} and ${newer} both keep ${the(item)}. DMbot won't ask about these two again. It's still one of a kind.`,
  undone: (older: string, item: string): string => `Undone. ${older} has ${the(item)} again.`,
  gone: "Already answered, maybe in Discord. Showing what is left.",
  notAllowed: "Only a DM of the campaign can answer this.",
  tooLate: "This can't be undone any more.",
  changed: "You've changed something since, so this can't be undone.",
  couldNot: "DMbot couldn't save that answer. Try again in a minute.",
  stillWorking: "DMbot is still working on your answer. Reload in a minute to see where it stands.",
} as const;

/** The question in plain lines (Discord's `card_text`, without the markup). */
export function cardOf(q: HandOverQuestion): { said: string; ask: string; noMeans: string; bothMeans: string } {
  return {
    said: `You said ${q.newerName} has ${the(q.itemName)} now. It's one of a kind.`,
    ask: `Does ${q.olderName} still have it?`,
    noMeans: `${q.olderName}'s part becomes history.`,
    bothMeans: "DMbot keeps both and won't ask again.",
  };
}

/** The bracket after a waiting connection that means an item changed hands (#4122), the same words as Discord's card. */
export const handBracket = (how: string, from: string): string =>
  ({ sold: `${from} sold it`, gave: `${from} gave it`, traded: `${from} traded it`, stolen: `taken from ${from}`, lost: `${from} lost it` } as Record<string, string>)[how] ?? "";

/** Why a hand-over has no Keep on the website yet: keeping it ends what the one before held, and Discord's card says so. */
export const handKeepInDiscord = "Keep this one in Discord, where the card shows how the item changed hands.";

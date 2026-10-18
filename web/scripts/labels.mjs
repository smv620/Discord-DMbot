// Which buttons the site tells people to press, and whether the bot really has them (#1893).
// Pure functions, so the vitest and the CI step share one reader; no file access here.

const unescape = (s) => s.replace(/\\(["'\\])/g, "$1");

/** Pull every button label out of one core Python file: `NAME_LABEL = "…"`, `NAME_BUTTON = "…"` and `label="…"`. */
export function labelsInPython(text) {
  const found = [];
  // Plain strings only: a label built with an f-string is filled in at run time and can't be read here.
  const constant = /^[ \t]*(?:[A-Z][A-Z0-9_]*_)?(?:LABEL|BUTTON)\s*(?::[^=\n]+)?=\s*\(?\s*"((?:[^"\\\n]|\\.)*)"/gm;
  for (const m of text.matchAll(constant)) found.push(unescape(m[1]));
  // `label="A" if x else "B"` names two buttons, so take every string on the label's line.
  for (const m of text.matchAll(/\blabel=([^\n]*)/g)) {
    for (const q of m[1].matchAll(/(?<![\w])"((?:[^"\\\n]|\\.)*)"/g)) found.push(unescape(q[1]));
  }
  return found;
}

/** Drop leading emoji and symbols and fold spacing, so "⚙️ Menu" and "Menu" compare equal. */
export function plain(s) {
  return s
    .replace(/^[^\p{L}\p{N}]+/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

// "then" is here because steps read "press X, then Y": Y is a button too. The capture sits in a
// lookahead so one phrase doesn't swallow the "then" that follows it.
const trigger = /\b(?:[Pp]ress|[Tt]ap|[Cc]hoose|[Pp]ick|then)\s+(?=((?:[\p{Extended_Pictographic}\uFE0F\u200D]\s*)*[A-Z][^"\n]*))/gu;

/** Every phrase the text presents as a button, with the words that follow it for matching. */
export function buttonPhrases(text) {
  const out = [];
  for (const m of text.matchAll(trigger)) {
    const rest = plain(m[1]).slice(0, 80);
    const phrase = rest.split(/[,.;:!?]| (?:and|to|in|on|so|if|when|for|then|again|at|before|after|until|or) /)[0].trim();
    out.push({ phrase, rest });
  }
  return out;
}

const words = (s) => new Set(plain(s).toLowerCase().split(/[^\p{L}\p{N}']+/u).filter(Boolean));

/** The real label that shares the most words with a phrase, for the error message. */
export function closestLabel(phrase, labels) {
  const mine = words(phrase);
  let best = "";
  let score = 0;
  for (const label of labels) {
    const shared = [...words(label)].filter((w) => mine.has(w)).length;
    if (shared > score) {
      best = label;
      score = shared;
    }
  }
  return best;
}

/**
 * Phrases that are not a bot label, a listed Discord control or the site's own button.
 * A phrase passes when a label begins the words that follow the verb, which lets labels
 * with a comma or an "and" in them ("Yes, stop recording me") through whole.
 */
export function labelProblems(items, labels, allowed = []) {
  const real = [...labels].map(plain);
  const ok = [...real, ...allowed.map(plain)];
  const begins = (rest) =>
    ok.some((label) => rest === label || (rest.startsWith(label) && !/^[\p{L}\p{N}]/u.test(rest.slice(label.length))));
  return items.filter(({ phrase, rest }) => phrase && !begins(rest)).map((i) => ({ ...i, closest: closestLabel(i.phrase, real) }));
}

/**
 * Labels the bot shows that the reader above can't see: they sit in a table, a tuple or a message
 * instead of `label=` or a `*_LABEL` constant. Each names the core file that holds it; the vitest
 * checks the text is still there, so a renamed button can't hide behind this list.
 */
export const hiddenBotLabels = [
  { label: "My buttons", file: "dm_screen/dm_bot_panel.py" },
  { label: "Species", file: "ui/homebrew.py" },
  { label: "Start a timer", file: "dm_screen/clock.py" },
  { label: "Rejoin the party", file: "dm_screen/clock.py" },
  { label: "See changes", file: "dm_screen/pack_update.py" },
  { label: "Pick changes", file: "dm_screen/pack_update.py" },
  { label: "See", file: "dm_screen/set_suggestion.py" },
  { label: "Add to set", file: "dm_screen/set_suggestion.py" },
  { label: "Help", file: "dm_screen/dm_bot_panel.py" },
  { label: "Game clock", file: "dm_screen/clock.py" },
  { label: "Rests & time…", file: "dm_screen/clock.py" },
  { label: "Split the party", file: "dm_screen/clock.py" },
  { label: "Time it", file: "dm_screen/rules_cards.py" },
  { label: "Share with ticked", file: "ui/shared_rules_defs.py" },
  { label: "Rules only", file: "ui/shared_rules_defs.py" },
  { label: "Turn FightBot off", file: "ui/sheets.py" },
  { label: "Off", file: "dm_screen/story_mode.py" },
  { label: "Secret: no", file: "ui/name_card.py" },
  { label: "Post to the table", file: "ui/recap_dm.py" },
];

/** Buttons and menu items of the website itself, which the bot's code can't hold. */
export const siteButtons = ["Cleaned", "House rules", "Session transcripts", "Forgot your password"];

/** Discord's own controls, which the bot doesn't label. */
export const discordControls = ["Save Changes", "Reset Token"];

// Which buttons the site tells people to press, and whether the bot really has them (#1893).
// Pure functions, so the vitest reads it; no file access here.

const unescape = (s) => s.replace(/\\(["'\\])/g, "$1");

/** Pull every button label out of one core Python file: `NAME_LABEL = "…"`, `NAME_BUTTON = "…"` and `label="…"`. */
export function labelsInPython(text) {
  const found = [];
  // Plain strings only: a label built with an f-string is filled in at run time and can't be read here.
  // The one exception is an f-string that only fills in whole-number constants from the same file
  // (the consent button's age), which is worked out here so the owner's checks can name it.
  const numbers = new Map([...text.matchAll(/^([A-Z][A-Z0-9_]*)\s*(?::[^=\n]+)?=\s*(\d+)[ \t]*(?:#.*)?$/gm)].map((m) => [m[1], m[2]]));
  const constant = /^[ \t]*(?:[A-Z][A-Z0-9_]*_)?(?:LABEL|BUTTON)\s*(?::[^=\n]+)?=\s*\(?\s*(f?)"((?:[^"\\\n]|\\.)*)"/gm;
  for (const m of text.matchAll(constant)) {
    const filled = m[1] ? m[2].replace(/\{([A-Z][A-Z0-9_]*)\}/g, (all, name) => numbers.get(name) ?? all) : m[2];
    // A name that isn't a same-file number stays in braces: skip it rather than invent a label.
    if (!m[1] || !/[{}]/.test(filled)) found.push(unescape(filled));
  }
  // `label="A" if x else "B"` names two buttons, so take every string on the label's line.
  for (const m of text.matchAll(/\blabel=([^\n]*)/g)) {
    for (const q of m[1].matchAll(/(?<![\w])"((?:[^"\\\n]|\\.)*)"/g)) found.push(unescape(q[1]));
  }
  return found;
}

/** Drop leading emoji and symbols and fold spacing, so "🗂️ Menu" and "Menu" compare equal. */
export function plain(s) {
  return s
    // ℹ️ is a lowercase letter to Unicode, so emoji are stripped by their own class first.
    .replace(/^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u, "")
    .replace(/^[^\p{L}\p{N}]+/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

// "then" is here because steps read "press X, then Y": Y is a button too. The capture sits in a
// lookahead so one phrase doesn't swallow the "then" that follows it.
const trigger = /\b(?:[Pp]ress|[Tt]ap|[Cc]hoose|[Pp]ick|then)\s+(?=((?:[\p{Extended_Pictographic}\uFE0F\u200D]\s*)*[A-Z][^"\n]*))/gu;

/** Markdown bold and code marks around a label are decoration, so a bold "**Helped**" reads as "Helped". */
export function unmark(text) {
  return text.replace(/\*\*|`/g, "");
}

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
  // Emoji-only and one-letter labels collapse to nothing useful and must never match a phrase.
  // A trailing "?" or "…" is how the button looks, not part of its name in a sentence.
  const real = [...labels].map((l) => plain(l).replace(/[?!]+$/u, "")).filter((l) => l.length >= 2);
  const ok = [...real, ...allowed.map(plain)];
  const same = (a, b) => a.toLowerCase() === b.toLowerCase();
  // The words up to the clause end are the label, or the text starts with a label that holds a
  // comma ("Yes, stop recording me") and then ends the clause or goes on with a joining word. A
  // label followed by more nouns ("Save changes" for "Save") is not a match.
  const begins = ({ phrase, rest }) =>
    ok.some((label) => same(phrase, label) || (rest.startsWith(label) && /^(?:$|[,.;:!?)]|\s*\(|\s+(?:and|to|in|on|so|if|when|for|then|again|at|before|after|until|or|under|only|first|there|here|any time)\b)/u.test(rest.slice(label.length))));
  return items.filter((i) => i.phrase && !begins(i)).map((i) => ({ ...i, closest: closestLabel(i.phrase, real) }));
}

/**
 * Labels the bot shows that the reader above can't see: they sit in a table, a tuple or a message
 * instead of `label=` or a `*_LABEL` constant. Each names the core file that holds it; the vitest
 * checks the text is still there, so a renamed button can't hide behind this list.
 */
export const hiddenBotLabels = [
  { label: "DMbot Main Menu", file: "dm_screen/dm_bot_panel.py" },
  { label: "Transcript retention", file: "dm_screen/transcript_keep.py" },
  { label: "Transcripts", file: "dm_screen/dm_bot_panel.py" },
  { label: "Manage campaign", file: "dm_screen/dm_bot_panel.py" }, // a link button built from LABELS (#2986)
  { label: "My data", file: "dm_screen/dm_bot_panel.py" },
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
  { label: "Watch the story", file: "dm_screen/story_mode.py" },
  { label: "Helped them", file: "memory/standing.py" },
  { label: "Next one is public", file: "ui/name_card.py" },
  { label: "Next one is secret", file: "ui/name_card.py" },
  { label: "Due soon", file: "memory/threads.py" },
  { label: "Post to the table", file: "ui/recap_dm.py" },
];

/** Buttons and menu items of the website itself, which the bot's code can't hold. */
export const siteButtons = [
  "Save the link", // the campaign invite panel (#2985), a website button, not in Discord
  "Remove the link", // the campaign invite panel (#2985), a website button, not in Discord
  "Cleaned",
  "House rules",
  "Session transcripts",
  "Forgot your password",
  "Merge selected", // the All names page (#2427), on the website, not in Discord
  "Change", // the recap page (#2577): changes who was in a scene, on the website, not in Discord
  "Save list", // the same panel: saves the scene's list (#2577)
  "Keep as a belief", // the Story graph's What people believe (#2412): keeps a reading as a belief, on the website, not in Discord
  "Drop it", // the same panel: says a reading is not wanted
  "Rewrite with these", // the recap page (#2114), on the website, not in Discord
  "Speak instead", // the recap page (#2114), on the website, not in Discord
  "Send", // the same page: sends the typed or spoken note
  // The recap page's share controls (#2119), on the website, not in Discord.
  // The recap maker for past sessions (#2610), on the website, not in Discord.
  "Make the recap",
  "Add a recap someone wrote",
  "Make a link",
  "Turn the link off",
  "Make a new link",
  "Remake the voice", // the recap page's audio part (#2630), on the website, not in Discord
  "Remake voice and music",
  "Replace everywhere", // "Fix a name everywhere" in the recap editor (#2630)
  "Save how to say it", // the Names page row editor (#2317), on the website, not in Discord
];

/** Buttons the site names before the bot has them: a held Help step written ahead of the code that
 * ships the button (#1958 for #1957). The test fails once core has the label, so the entry is
 * removed in the same change that sees it, and a typo here can't hide a wrong name for long. */
export const pendingBotLabels = /** @type {{ label: string; issue: number }[]} */ ([]);

/** Discord's own controls, which the bot doesn't label. */
export const discordControls = ["Save Changes", "Reset Token"];

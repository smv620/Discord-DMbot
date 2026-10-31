// The words of the Main rules panel on a campaign's page (#2985): which rules DMbot goes by first, and what it uses when
// they do not cover something. The same two choices as Discord's picker (core campaigns/models.py RULESETS, FALLBACK_NONE;
// a test keeps the lists in step). DMbot advises; the game master decides which rules the table uses.
export const MAIN_CHOICES = ["2024", "2014"] as const;
export const FALLBACK_NONE = "none";
export type Main = (typeof MAIN_CHOICES)[number];
export type Fallback = Main | typeof FALLBACK_NONE;

export const rulesetsPanel = {
  heading: "Main rules",
  intro: "DMbot checks your main rules first. If they don't cover something, it can use a second set of rules or skip it. DMbot only points out what the rules say; you decide.",
  mainLegend: "Which rules come first?",
  main: { "2024": "2024 rules (newest)", "2014": "2014 rules (older)" } as Record<Main, string>,
  fallbackLegend: "If the main rules don't cover something",
  fallback: {
    "2024": "Use the 2024 rules",
    "2014": "Use the 2014 rules",
    none: "Skip it, use only the main rules",
  } as Record<Fallback, string>,
  // A status sentence, not a button label: what DMbot will do, in the page's own words.
  fallbackNow: { "2024": "use the 2024 rules", "2014": "use the 2014 rules", none: "skip it" } as Record<Fallback, string>,
  now: (main: Main, fallback: Fallback): string => `Now: main rules are ${rulesetsPanel.main[main]}. If they don't cover something, DMbot will ${rulesetsPanel.fallbackNow[fallback]}.`,
  still: (main: Main, fallback: Fallback): string => `Still: main rules are ${rulesetsPanel.main[main]}; if they don't cover something, DMbot will ${rulesetsPanel.fallbackNow[fallback]}.`,
  save: "Save",
  waiting: "Waiting for DMbot to pick this up.",
  working: "DMbot is saving your rules.",
  done: "Saved. DMbot uses these rules from now on.",
  failed: "Nothing was changed.",
  failedNext: "Try again in a minute. If it keeps happening, ask for help.",
  timedOut: "DMbot hasn't answered yet. It may still save this. Reload the page in a minute to see where it got to.",
  problems: {
    "not-allowed": "Only a DM of this campaign can do this. Ask your DM.",
    "bad-value": "The second set of rules can't be the same as your main rules. Pick the other set, or choose to skip it.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "DMbot can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong and nothing was changed. Try again in a minute, or ask for help if it keeps happening.",
};

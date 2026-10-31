// The words of the Main rules panel on a campaign's page (#2985): which rules DMbot goes by first, and what it uses when
// they do not cover something. The same two choices as Discord's picker (core campaigns/models.py RULESETS, FALLBACK_NONE;
// a test keeps the lists in step). DMbot advises; the game master decides which rules the table uses.
export const MAIN_CHOICES = ["2024", "2014"] as const;
export const FALLBACK_NONE = "none";
export type Main = (typeof MAIN_CHOICES)[number];
export type Fallback = Main | typeof FALLBACK_NONE;

export const rulesetsPanel = {
  heading: "Main rules",
  intro: "DMbot checks your main rules first. If they don't cover something, it can look in a second set of rules, or skip it. It only tells you what the rules say; you decide what the table does.",
  mainLegend: "Main rules",
  main: { "2024": "2024 rules (newest)", "2014": "2014 rules (older)" } as Record<Main, string>,
  fallbackLegend: "If the main rules don't cover something",
  fallback: {
    "2024": "Use the 2024 rules",
    "2014": "Use the 2014 rules",
    none: "Skip it, use only the main rules",
  } as Record<Fallback, string>,
  now: (main: Main, fallback: Fallback): string => `Now: ${rulesetsPanel.main[main]}. ${rulesetsPanel.fallback[fallback]}.`,
  save: "Save",
  waiting: "Saving your rules. DMbot will do it in a moment.",
  working: "Saving your rules now.",
  done: "Saved. DMbot uses these rules from now on.",
  failed: "Nothing was changed.",
  failedNext: "Try again in a minute.",
  timedOut: "DMbot hasn't answered yet. It may still do it. Check back in a minute; the line above changes when it has.",
  problems: {
    "not-allowed": "Only a DM of this campaign can do this. Ask your DM.",
    "bad-value": "Those two can't go together. The second set has to be a different set from the main rules, or skipped.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "DMbot can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong and nothing was changed. Try again, or ask for help if it keeps happening.",
};

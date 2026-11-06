// The words of the Shared story choice on a campaign's page (#2985): Off, Watch the story or Guide me, and the
// right-to-use confirmation that turning it on needs (CLAUDE.md, IP rule; docs/PLAN.md, "Shared story switch"). The
// same words as Discord's panel; DMbot only advises, the game master decides.
export type StoryMode = "off" | "watch" | "guide";

export const storyModePanel = {
  heading: "Shared story",
  intro: "DMbot can follow your adventure's story. It tells you on your DM screen, never at the table. It never makes up story and never decides anything: you do.",
  whereMaterial: "Once it is on, you give DMbot the adventure itself in Discord, with the 📖 Give your adventure button on the Shared story card.",
  modes: {
    off: { label: "Off", help: "DMbot doesn't follow the adventure's story." },
    watch: { label: "Watch the story", help: "DMbot warns you on your DM screen, once, if someone the adventure needs is gone or something it needs is lost." },
    guide: { label: "Guide me", help: "Does Watch too, and you can ask DMbot what comes next in your adventure. Only you see the answer." },
  } as Record<StoryMode, { label: string; help: string }>,
  now: (mode: StoryMode): string => `Now: ${storyModePanel.modes[mode].label}.`,
  choose: (mode: StoryMode): string => `Choose ${storyModePanel.modes[mode].label}`,
  turnOn: (mode: StoryMode): string => `Turn on: ${storyModePanel.modes[mode].label}`,
  warning: "Only give DMbot material you have the right to use. DMbot doesn't check this.",
  rightLabel: "I have the right to use the material I give DMbot.",
  confirm: "Turn it on",
  keep: "Keep it as it is",
  waiting: "Saving your choice. DMbot will do it in a moment.",
  working: "Saving your choice now.",
  done: (mode: StoryMode): string => `Saved. ${storyModePanel.now(mode)}`,
  failed: "Nothing was changed. Try again in a minute.",
  stillThat: (mode: StoryMode): string => `It is still ${storyModePanel.modes[mode].label}.`,
  timedOut: "DMbot hasn't answered yet. It may still do it. Reload the page in a minute to see where it got to.",
  problems: {
    "not-allowed": "Only a campaign's game master can do this. Ask your game master.",
    "needs-confirm": "Tick the box first to say you have the right to use the material.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong and nothing was changed. Try again, or ask for help if it keeps happening.",
};

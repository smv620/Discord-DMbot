// The words of the Shared story choice on a campaign's page (#2985): Off, Watch the story or Guide me, and the
// right-to-use confirmation that turning it on needs (CLAUDE.md, IP rule; docs/PLAN.md, "Shared story switch"). The
// same words as Discord's panel; DMbot only advises, the game master decides.
export type StoryMode = "off" | "watch" | "guide";

export const storyModePanel = {
  heading: "Shared story",
  intro: "Tell DMbot what adventure you are playing, and it can watch for trouble or say what comes next. It only tells you, never the players.",
  modes: {
    off: { label: "Off", help: "DMbot doesn't follow the adventure's story." },
    watch: { label: "Watch the story", help: "DMbot tells you, once, when the table breaks something the adventure still needs." },
    guide: { label: "Guide me", help: "DMbot can also tell you what the story has next." },
  } as Record<StoryMode, { label: string; help: string }>,
  now: (mode: StoryMode): string => `Now: ${storyModePanel.modes[mode].label}.`,
  choose: (mode: StoryMode): string => `Choose ${storyModePanel.modes[mode].label}`,
  warning: "Only give DMbot material you have the right to use. DMbot doesn't check this.",
  rightLabel: "I have the right to use the material I give DMbot.",
  confirm: "Turn it on",
  keep: "Keep it as it is",
  confirmLabel: "Confirm",
  waiting: { on: "Turning the story switch on. DMbot will do it in a moment.", off: "Turning the story switch off. DMbot will do it in a moment." },
  working: { on: "Turning the story switch on now.", off: "Turning the story switch off now." },
  done: { on: "Saved. DMbot now follows the story.", off: "Saved. DMbot no longer follows the story." },
  failed: "Nothing was changed. Try again in a minute.",
  stillThat: (mode: StoryMode): string => `It is still ${storyModePanel.modes[mode].label}.`,
  timedOut: "DMbot hasn't answered yet. It may still do it. Check back in a minute.",
  problems: {
    "not-allowed": "Only a campaign's game master can do this. Ask your game master.",
    "needs-confirm": "Tick the box first to say you have the right to use the material.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong. Try again.",
};

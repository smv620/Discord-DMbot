// The words of the Story Graph's settings panel (#3620). Plain words; the choices belong to the viewer, are kept in this
// browser only, and change nothing in the campaign.
export const graphSettingsWords = {
  heading: "Graph settings",
  lead: "These are only for you, and stay in this browser.",
  kindsHeading: "Show only these kinds",
  kindsHint: "Tick none to show every kind.",
  kindCount: (label: string, n: number): string => `${label} (${n})`,
  spacing: "Spacing for Spread out",
  tighter: "Tighter",
  moreSpread: "More spread out",
  nodeSize: "Size of names",
  textSize: "Size of words",
  smaller: "Smaller",
  bigger: "Bigger",
  lineWidth: "Thicker lines for more connections",
  lineWidthHint: "A line between two names gets thicker the more connections it holds, up to four.",
  reset: "Reset to defaults",
  resetDone: "Back to the usual look.",
} as const;

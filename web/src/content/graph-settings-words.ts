// The words of the Story Graph's settings panel (#3620). Plain words; the choices belong to the viewer, are kept in this
// browser only, and change nothing in the campaign.
export const graphSettingsWords = {
  heading: "Graph settings",
  lead: "These are only for you. They stay in this browser and change nothing in the campaign.",
  kindsHeading: "Show only these kinds",
  kindsHint: "Tick none to show every kind. The middle name always stays.",
  kindCount: (label: string, n: number): string => `${label} (${n})`,
  spacing: "Space between names",
  spacingHint: "How far apart the names are drawn.",
  tighter: "Tighter",
  moreSpread: "More spread out",
  nodeSize: "Size of the shapes",
  textSize: "Size of the text",
  usual: "Usual",
  percent: (n: number): string => `${Math.round(n * 100)} percent`,
  smaller: "Smaller",
  bigger: "Bigger",
  lineWidth: "Thicker lines where there are more connections",
  lineWidthHint: "Lines with more connections are drawn thicker, up to 4.",
  reset: "Reset to defaults",
  resetDone: "Back to the usual look.",
} as const;

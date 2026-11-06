// The words of the Icon row on a player character's details panel (#3622). Plain words. The icon is the DM's and the PC's
// own player's to change; nobody else sees these buttons.
export const pcIconWords = {
  heading: "Icon",
  scope: "The picture shown for this character on the story map.",
  showing: {
    default: "Showing the Discord picture. If there isn't one, the D&D Beyond portrait. If neither, a plain person.",
    discord: "Showing the Discord picture.",
    dndbeyond: "Showing the D&D Beyond portrait.",
    hidden: "No icon is shown.",
  } as Record<string, string>,
  useDiscord: "Use Discord",
  useBeyond: "Use D&D Beyond",
  remove: "Remove",
  removeHint: "Remove goes back to the usual order.",
  hide: "Hide the icon",
  done: "Saved.",
  undo: "Undo",
  undoLabel: "Undo the icon change",
  undone: "Undone.",
  working: "Saving…",
  problems: {
    unavailable: "That picture isn't there any more. The page is up to date now. Pick another one.",
    "not-allowed": "Only the DM, or the player of this character, can change the icon.",
    "not-found": "That character isn't here any more. The page is up to date now.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
    server: "That didn't work. Try again in a minute.",
  } as Record<string, string>,
  alt: (name: string): string => `Icon of ${name}`,
} as const;

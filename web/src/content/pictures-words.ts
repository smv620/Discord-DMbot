// The words of the Pictures page (#3274, #3276): what the table's kept pictures are, and what a game master or the
// poster may do to one. Plain words; the page never says how a picture was made or where it is stored.
export const picturesPage = {
  heading: "Pictures",
  intro:
    "Pictures people posted in your table's chat. Anyone with a picture's link can open it, until it is made private or deleted.",
  notOpen: "This page is not open yet.",
  loading: "Loading the pictures…",
  failed: "Couldn't load the pictures.",
  tryAgain: "Try again",
  none: "No pictures yet.",
  noMatch: "No pictures match.",
  sessionLabel: "Session",
  allSessions: "All sessions",
  sessionOption: (n: number): string => `Session ${n}`,
  posterLabel: "Posted by",
  everyone: "Everyone",
  searchLabel: "Search the descriptions",
  search: "Search",
  showMore: "Show more",
  usage: (used: number, limit: number, permanent: number, permanentLimit: number): string =>
    `${used} MB of ${limit} MB used · ${permanent} MB of ${permanentLimit} MB permanent`,
  sessionTag: (n: number): string => `Session ${n}`,
  privateTag: "Private to this campaign",
  permanentTag: "📌 Permanent",
  permanent: "Permanent",
  notPermanent: "Not permanent",
  makePermanent: (description: string): string => `Make the picture '${description}' permanent`,
  stopPermanent: (description: string): string => `Stop keeping the picture '${description}' permanent`,
  makePrivate: "Make private",
  makePublic: "Make public",
  remove: "Delete",
  confirmDelete: "Delete this picture for good?",
  confirmRemove: "Yes, delete it",
  roomNote: "When space runs out, the oldest pictures that aren't permanent are removed to make room.",
  cancel: "Cancel",
  permanentFull: (limit: number | null): string =>
    limit === null
      ? "Permanent pictures are full. Unmark one to make room."
      : `Permanent pictures are full (${limit} MB). Unmark one to make room.`,
  actionFailed: "Couldn't do that. Try again.",
  working: "Working on it. This can take a minute.",
  // The wait for the bot (#3347): the stage, and what to do once it looks stuck.
  waitingStage: "Waiting for DMbot to pick it up.",
  stuck: "DMbot hasn't finished this one. It may still go through. Reload the page in a few minutes to see where it stands before you try again.",
  notAllowed: "Only the DM can do that.",
} as const;

// The words of the "one place written two ways" question on the Names page (#3533), the same as Discord's (#3532):
// a new spelling of a place the campaign already has is asked about, never joined silently. Nothing changes until the
// DM answers; a No is remembered so the pair is not offered again. DM only. Plain words, no technical terms.
export const placeSpellingWords = {
  heading: "Is this the same place?",
  lead: "Nothing changes until you choose.",
  // "North Haven looks like another way of writing Northhaven." (the names are bold on the page)
  looksPrefix: "",
  looksMiddle: " looks like another way of writing ",
  looksSuffix: ".",
  yes: "Yes, the same place",
  no: "No, two places",
  yesFor: (spelling: string, place: string): string => `Yes, ${spelling} is the same place as ${place}`,
  noFor: (spelling: string, place: string): string => `No, ${spelling} and ${place} are two places`,
  working: "Saving your answer…",
  saidYes: (spelling: string, place: string): string => `${spelling} is now another name for ${place}.`,
  saidNo: (spelling: string, place: string): string => `Kept apart. DMbot won't ask again about ${spelling} and ${place}.`,
  failed: "That didn't save. Try again. If it keeps failing, ask on the Help page.",
  loadFailed: "DMbot couldn't check for places written two ways. Reload the page to try again.",
} as const;

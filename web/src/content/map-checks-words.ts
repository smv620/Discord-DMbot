// The words of the website's "Map checks" switch on a campaign's Settings (#4687), the same shape as share-story-words.ts.
// Only a game master changes it; turning it on or off asks nothing. The Discord settings card says the same in its own words.
export const mapChecksWords = {
  heading: "🗺️ Map checks",
  lead: "When a distance or direction you say at the table is far off a map, DMbot tells you privately, once per pair of places, and it sends its own estimate when you say a travel time. You only get these notes once the map answers are on.",
  on: "Map checks: on",
  off: "Map checks: off",
  saved: "Saved.",
  waiting: "Waiting for DMbot to pick it up.",
  working: "DMbot is changing it.",
  quiet: "DMbot is slow to answer. Your choice is still waiting. You can leave this page and check back later.",
  failed: "That didn't work, and nothing was changed. Try again in a moment.",
  problems: {
    "not-allowed": "Only a game master of this campaign can change this.",
  } as Record<string, string>,
};

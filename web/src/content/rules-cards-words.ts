// The words of the website's "Rules cards" switch on a campaign's Settings (#5155), the same shape as map-checks-words.ts.
// Only a game master changes it; turning it on or off asks nothing. The Discord settings card says the same in its own words.
export const rulesCardsWords = {
  heading: "📖 Rules cards",
  lead: "When someone who agreed to be recorded names a spell, condition or creature, DMbot shows a short card on your DM screen (one a minute at most). It only shows the free rules (SRD); you decide. Players who peek behind the DM screen can see them too.",
  on: "Rules cards: on",
  off: "Rules cards: off",
  saved: "Saved.",
  waiting: "Waiting for DMbot to pick it up.",
  working: "DMbot is changing it.",
  quiet: "DMbot is slow to answer. Your choice is still waiting. You can leave this page and check back later.",
  failed: "That didn't work, and nothing was changed. Try again in a moment.",
  problems: {
    "not-allowed": "Only a game master of this campaign can change this.",
  } as Record<string, string>,
};

// The words of Pause / Unpause on a campaign's page (#2985). Only the campaign's owner has the button: it is their plan's
// place that changes (Discord's rule, dm_screen/pause.py). The other game masters see the state and are told to ask.
export const pausePanel = {
  heading: "Pause this campaign",
  pausedNow: "This campaign is paused. It can't start. Everything is kept.",
  runningNow: "This campaign is not paused.",
  pauseButton: "Pause this campaign",
  unpauseButton: "Unpause",
  pauseAsk: "Pausing keeps everything but stops it from starting, and frees a place on your plan.",
  unpauseAsk: "Unpausing lets it start again and uses a place on your plan.",
  yesPause: "Yes, pause it",
  yesUnpause: "Yes, unpause it",
  keep: "Never mind",
  waiting: { pause: "Pausing the campaign. DMbot will do it in a moment.", unpause: "Unpausing the campaign. DMbot will do it in a moment." },
  working: { pause: "Pausing the campaign now.", unpause: "Unpausing the campaign now." },
  done: { pause: "Paused. The campaign can't start until you unpause it.", unpause: "Unpaused. The campaign can start again." },
  notOwner: "Only the campaign's owner can pause or unpause it. Ask them to.",
  failed: "Nothing was changed. DMbot couldn't do that. Try again.",
  problems: {
    "not-allowed": "Only a campaign's game master can do this.",
    "not-owner": "Only the campaign's owner can pause or unpause it. Ask them to.",
    "too-soon": "DMbot is still catching up on your earlier changes. Wait a minute, then try again.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong. Try again.",
};

// The words of Pause / Unpause on a campaign's page (#2985). Only the campaign's owner has the button: it is their plan's
// place that changes (Discord's rule, dm_screen/pause.py). The other game masters see the state and are told to ask.
export const pausePanel = {
  pausedNow: "Paused. This campaign can't start. Nothing is deleted.",
  runningNow: "Not paused. This campaign can start.",
  pauseButton: "Pause this campaign",
  unpauseButton: "Unpause this campaign",
  pauseAsk: "Pause this campaign? It can't start while it is paused. Nothing is deleted, and a paused campaign doesn't count toward your plan.",
  unpauseAsk: "Unpause this campaign? It can start again and counts toward your plan again. If your plan is full, it can't be unpaused.",
  yesPause: "Yes, pause it",
  yesUnpause: "Yes, unpause it",
  keep: "Keep it as it is",
  confirmLabel: "Confirm",
  waiting: { pause: "Pausing the campaign. DMbot will do it in a moment.", unpause: "Unpausing the campaign. DMbot will do it in a moment." },
  working: { pause: "Pausing the campaign now.", unpause: "Unpausing the campaign now." },
  done: { pause: "Paused. The campaign can't start until you unpause it.", unpause: "Unpaused. The campaign can start again." },
  failed: "Nothing was changed. Try again in a minute.",
  stillPaused: "The campaign is still paused.",
  stillRunning: "The campaign is still not paused.",
  noRoomNext: "To unpause it, free a place on your plan (pause or remove another campaign), then try again.",
  timedOut: "DMbot hasn't answered yet. It may still do it. Check back in a minute.",
  problems: {
    "not-allowed": "Only a campaign's game master can do this. Ask your game master.",
    "not-owner": "Only the campaign's owner can pause or unpause it. Ask them to.",
    "too-soon": "Your last change is still being applied. Wait a minute, then try again.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong. Try again.",
};

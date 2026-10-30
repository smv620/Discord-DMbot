// The words of the House-rules file panel on a campaign's House rules page (#2985, first slice): connect or change the
// file, stop using it, compare it now, or upload a text file once. Counts only, never a rule; Review and Accept stay in
// Discord for now, so every result that finds a difference says exactly where to go next. The button names are
// Discord's own (ui/house_file.py), so the same words lead the game master from here to there.
export const HOUSE_FILE_TEXT_MAX = 200_000;
export const HOUSE_FILE_LINK_MAX = 2000;

const DISCORD_PATH = "In Discord, press ⚙️ Manage DMbot, then 📄 House-rules file, then 🔄 Compare now.";

export const houseFilePanel = {
  heading: "House-rules file",
  intro: "Keep your house rules in a file you can read anywhere. DMbot compares it with your campaign's rules and tells you what is different. It never changes a rule on its own.",
  connected: (site: string): string => `Connected: ${site}. DMbot looks at it at the start of every session.`,
  notConnected: "No file is connected yet.",
  connect: "Connect a file",
  change: "Use another file",
  compare: "Compare now",
  stop: "Stop using this file",
  upload: "Upload a text file once",
  linkLabel: "Link to your rules (Doc, PDF, web page)",
  linkHelp: "A share link that anyone with it can open. It starts with https://. DMbot never shows it back.",
  linkSend: "Connect it",
  uploadHelp: "A .txt file, up to 200,000 letters. For a PDF, Word or Excel file, use Discord: ⚙️ Manage DMbot, then 📄 House-rules file, then 📎 Upload a file once.",
  uploadSend: "Compare this file",
  stopAsk: "Stop using this file? DMbot keeps your rules as they are. It only stops comparing.",
  stopYes: "Yes, stop using it",
  keep: "Keep it",
  cancel: "Cancel",
  confirmLabel: "Confirm",
  waiting: {
    link: "Connecting your file. DMbot will do it in a moment.",
    unlink: "Stopping. DMbot will do it in a moment.",
    check: "Comparing your file with your rules. DMbot will start in a moment.",
    upload: "Comparing your file with your rules. DMbot will start in a moment.",
  } as Record<string, string>,
  stages: {
    waiting: "Waiting for DMbot to pick it up",
    fetching: "Opening your file",
    reading: "Reading your rules",
    comparing: "Comparing with your campaign's rules",
    done: "Done",
  } as Record<string, string>,
  results: {
    link: "Connected. DMbot will look at it at the start of every session. Press Compare now to check it now.",
    unlink: "Stopped. DMbot no longer looks at that file. Your rules stay as they are.",
  } as Record<string, string>,
  matches: "Your file and your campaign's rules match. Nothing to change.",
  // Only the counts that are not zero, so "1 to add, 3 to remove." and never "0 to change".
  counts: (add: number, change: number, remove: number): string =>
    `${[add > 0 ? `${add} to add` : "", change > 0 ? `${change} to change` : "", remove > 0 ? `${remove} to remove` : ""].filter(Boolean).join(", ")}.`,
  removing: "Removing means a rule in DMbot that your file doesn't have. Review shows each one first.",
  next: "DMbot is putting the choice on your DM screen in Discord. Look for Review, Accept all, or Not now. Nothing changes until you press one.",
  nextFallback: `If you can't see it, ${DISCORD_PATH.charAt(0).toLowerCase()}${DISCORD_PATH.slice(1)}`,
  failed: "Nothing was changed.",
  failedReasons: {
    not_taken: "DMbot didn't pick this up in time. Try again in a minute.",
    unreadable: "DMbot couldn't read that file. Check that anyone with the link can open it, then try again.",
    bad_link: "That doesn't look like a link DMbot can open. Paste the share link of your file.",
    too_big: "That file is too big for DMbot to read. Use a shorter file, or split it in two.",
    no_rules: "DMbot found no rules in that file, so it changed nothing. Check that your rules are in the file, one per line or numbered.",
    busy: "DMbot is already comparing this file. Wait a minute, then look again.",
    too_often: "DMbot just compared this file. Try again in a few seconds.",
    daily_limit: "DMbot has read as many files as it can today. Try again tomorrow.",
    no_ai: "Your plan can only read plain numbered lists. Use a numbered list.",
    no_offer: "DMbot couldn't post the choice to your DM screen.",
    not_dm: "Only a DM of this campaign can do this.",
  } as Record<string, string>,
  failedFallback: "DMbot couldn't finish this.",
  failedNext: `You can also do it in Discord. ${DISCORD_PATH}`,
  stalled: `DMbot hasn't moved on this for a while. It may be busy. You can wait, or in Discord press ⚙️ Manage DMbot, then 📄 House-rules file, then 🔄 Compare now.`,
  tooBig: "That file is too long. Use a file up to 200,000 letters.",
  fileUnreadable: "That doesn't look like a plain text file. Use a .txt file, or upload it in Discord.",
  nothingYet: "Add a link or a file first.",
  problems: {
    "not-allowed": "Only a DM of this campaign can do this. Ask your DM.",
    "bad-text": "DMbot can't use that. Check the link starts with https:// and has no spaces.",
    "ticket-too-long": "That is too long. A link can be up to 2,000 letters; a file up to 200,000.",
    "too-soon": "That's a lot of requests in an hour. Try again a little later.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  genericProblem: "Something went wrong and nothing was changed. Try again, or ask for help if it keeps happening.",
  loading: "Looking at your house-rules file…",
  loadFailed: "We couldn't look at your house-rules file just now. Nothing was changed.",
  tryAgain: "Try again",
  lookFailed: "We lost sight of this request, but DMbot may still be doing it. Reload the page in a minute to see where it got to.",
};

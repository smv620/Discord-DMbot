// The words of the "Load a backup" page (#2745; docs/PLAN.md, "Everything on the website except picking Discord
// channels"). Plain words: a "copy" is the file from "Download a copy" in Discord or on the website. The page only
// checks what it can see in the file; DMbot checks the plan, the free campaign slot and the rights again when it
// loads it, and says what to do if it can't.
//
/** The limits Discord's `/dmbot restore` has (core campaigns/store.py MAX_BACKUP_BYTES, campaigns/parts.py
 * PART_READ_BYTES, web/restores.py MAX_PARTS). The API says no past them; the page says so first. */
export const RESTORE_MAX_MB = 25;
export const RESTORE_MAX_PARTS = 24;

export const restoreWords = {
  heading: "Load a backup",
  intro:
    "Bring a campaign back from a copy you downloaded. You become its DM. DMbot doesn't start listening: use /dmbot start in Discord when you are ready to play.",
  loading: "Loading…",
  failedToLoad: "We couldn't load this page. Reload it, or try again in a minute.",
  unavailable: "Loading a backup here isn't switched on yet. You can use /dmbot restore in Discord.",
  noServers:
    "DMbot isn't in any of your servers yet. Add it to a server first, then come back here.",
  signedOut: "Sign in to load a backup.",
  serverLabel: "Which server should the campaign go in?",
  whereLegend: "What should the copy become?",
  asNew: "A new campaign",
  asReplace: "Instead of one of my campaigns",
  replaceLabel: "Which campaign should the copy replace?",
  noneToReplace: "You don't run a campaign in this server yet, so the copy can only become a new one.",
  fileLabel: "The copy",
  fileHint: `The file you got from Download a copy (it ends in .dmbot.json.gz). Up to ${RESTORE_MAX_MB} MB.`,
  checkFile: "Check the file",
  checking: "Checking the file…",
  pickFirst: "Pick the copy's file first.",
  partsHeading: (name: string): string => `The copy of ${name}`,
  partsText: (n: number): string =>
    `This copy has its transcripts in ${n} more ${n === 1 ? "file" : "files"}. Pick all ${n === 1 ? "of it" : `${n} of them`} (they came with the copy), or none to bring the campaign back without its transcripts.`,
  partsLabel: "The transcripts files",
  partsPicked: (n: number, of: number): string => `${n} of ${of} picked`,
  partsWrongCount: (n: number, of: number): string =>
    `You picked ${n}, and this copy has ${of}. Pick all ${of}, or none.`,
  partsSending: (n: number, of: number): string => `Sending file ${n} of ${of}…`,
  sendParts: "Send the files",
  withoutParts: "Bring it back without the transcripts",
  noTranscriptsNote:
    "It will come back without its transcripts. To add them later, load the copy again with all of its files.",
  theCampaign: "the campaign you picked",
  confirmHeading: "Ready to load it",
  confirmNew: (name: string): string => `This will make a new campaign from the copy of ${name}. You will be its DM.`,
  confirmReplace: (name: string, target: string): string =>
    `This will replace everything in ${target} with the copy of ${name}. It can't be undone.`,
  downloadFirst: "Download it first",
  yes: "Load it",
  yesReplace: "Yes, replace it",
  cancel: "Cancel",
  cancelled: "Nothing was changed.",
  working: "Working…",
  // The stages the API names (core progress.py).
  stage: {
    restore_receiving: "Waiting for your transcripts files.",
    restore_waiting: "Waiting for DMbot to pick it up.",
    restore_reading: "DMbot is checking the copy.",
    restore_writing: "DMbot is putting the campaign back.",
    done: "Done.",
    failed: "That didn't work.",
  } as Record<string, string>,
  doneTitle: (name: string): string => `${name} is back.`,
  doneNew: "Use /dmbot start in Discord to pick its voice channel and play.",
  doneReplaced: "Everything in the campaign now comes from the copy.",
  openCampaign: "Open the campaign",
  leftOut: (n: number): string =>
    `${n} ${n === 1 ? "line" : "lines"} of people who have since deleted their transcripts ${n === 1 ? "was" : "were"} left out, as they asked.`,
  startOver: "Load another",
  checkAgain: "Check again",
  stopped: "The page has stopped checking. Press Check again to look.",
  gone: "That restore is gone. It was cleared or it ran out of time. Start again.",
  expires: "The file is kept for up to 24 hours and deleted when it is loaded or cleared.",
  // Why it couldn't be loaded (the bot's words, as codes).
  reasons: {
    plan: "Your plan can't load a copy here, or it has no free campaign slot. Look at your plan on My Account.",
    not_allowed: "You are not in that server any more, or you are no longer a DM of that campaign.",
    not_found: "The campaign it was to replace is gone.",
    playing: "That campaign is playing right now. Stop the session in Discord, then load the copy again.",
    not_a_backup: "That file isn't a DMbot backup.",
    too_big: `That file is too big. A copy can be up to ${RESTORE_MAX_MB} MB.`,
    parts: "A transcripts file was missing, repeated or from another copy. Load it again with all of its files, or none.",
    stalled: "Nothing new for a while.",
    failed: "DMbot couldn't load it. Try again; if it fails again, ask on the Help page.",
  } as Record<string, string>,
  // Said when the API refuses before anything is sent.
  problems: {
    "not-a-backup": "That file isn't a DMbot backup. Pick the file you got from Download a copy.",
    "too-big": `That file is too big. A copy can be up to ${RESTORE_MAX_MB} MB.`,
    "too-many-parts": `This copy has more than ${RESTORE_MAX_PARTS} transcripts files, which is more than can be loaded here.`,
    "bad-part": "That transcripts file doesn't belong to this copy.",
    "files-missing": "Some of the transcripts files haven't arrived. Send them all, or none.",
    "not-receiving": "This copy has already gone to DMbot.",
    "too-soon": "You have loaded several copies in the last hour. Try again later.",
    "slow-down": "You already have a copy on its way. It is shown below.",
    "not-found": "We couldn't find that. Pick the file again.",
    "not-allowed": "You can't load a copy into that server.",
    "signed-out": "You were signed out. Sign in and try again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
};

/** The plain words for a refused call, or the general ones. */
export function restoreProblem(kind: string): string {
  return Object.hasOwn(restoreWords.problems, kind)
    ? (restoreWords.problems[kind] as string)
    : (restoreWords.problems["server"] as string);
}

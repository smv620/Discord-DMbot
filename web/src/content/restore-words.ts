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
    "Bring a campaign back from a backup copy you downloaded. You become its DM.",
  loading: "Loading…",
  failedToLoad: "We couldn't load this page. Reload it, or try again in a minute.",
  unavailable: "Loading a backup here isn't switched on yet. You can use /dmbot restore in Discord.",
  noServers:
    "DMbot isn't in any of your servers yet. Add it to a server first, then come back here.",
  signedOut: "Sign in to load a backup.",
  serverLabel: "Which server should the campaign go in?",
  whereLegend: "What should the copy become?",
  asNew: "A new campaign",
  asReplace: "Replace one of my campaigns",
  replaceLabel: "Which campaign should the copy replace?",
  noneToReplace: "You don't run a campaign in this server yet, so the copy can only become a new one.",
  fileLabel: "The copy",
  fileHint: `Pick the backup file you downloaded (look in your Downloads). Up to ${RESTORE_MAX_MB} MB.`,
  checkFile: "Check the file",
  checking: "Checking the file… Keep this page open. This can take a minute on a phone.",
  pickFirst: "Pick the copy's file first.",
  partsHeading: (name: string): string => `The backup of ${name}`,
  loadingHeading: (name: string): string => `Loading ${name}`,
  partsText: (n: number): string =>
    `Your backup came with ${n} extra ${n === 1 ? "file" : "files"} of session transcripts. ${n === 1 ? "Pick it" : `Pick all ${n}`}, or press Skip the transcripts.`,
  partsLabel: "The transcripts files",
  partsPicked: (n: number, of: number): string => `${n} of ${of} picked`,
  partsWrongCount: (n: number, of: number): string =>
    `You picked ${n}, and this backup has ${of}. Pick all ${of}, or press Skip the transcripts.`,
  partsSending: (n: number, of: number): string => `Sending file ${n} of ${of}… Keep this page open.`,
  sendParts: "Send the files",
  withoutParts: "Skip the transcripts",
  noTranscriptsNote:
    "It will come back without its session transcripts. They can't be added afterwards: you would have to load the whole backup again.",

  confirmHeading: "Ready to load it",
  confirmNew: (name: string, server: string): string =>
    `This will make a new campaign from the backup of "${name}" in ${server}. You will be its DM.`,
  confirmReplace: (name: string, target: string): string =>
    `Replace "${target}"? Everything in it (story, notes, names, transcripts) is swapped for the backup of "${name}". This can't be undone. You will be its DM, and any other DMs lose access.`,
  lostTrack: "We lost track of which campaign this was for. Cancel and start again.",
  downloadFirst: "Save a copy first",
  downloadNote: "Opens in a new tab. Come back here after.",
  yes: "Load it",
  yesReplace: "Replace campaign",
  cancel: "Cancel",
  cancelled: "Nothing was changed.",
  working: "Sending to DMbot… Keep this page open.",
  // The stages the API names (core progress.py).
  stage: {
    restore_receiving: "Waiting for your transcripts files.",
    restore_waiting: "Waiting for DMbot to pick it up.",
    restore_reading: "DMbot is checking the copy.",
    restore_writing: "DMbot is putting the campaign back.",
    done: "Done.",
    failed: "That didn't work. See below.",
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
  lastingNote: "If you are replacing a campaign and it fails, say so on the Help page.",
  gone: "That restore is gone. It was cleared or it ran out of time. Start again.",
  expires: "Your file is only kept for 24 hours, and deleted after it is loaded or cancelled.",
  keepOpen: "Once it says it is with DMbot you can close this page and come back.",
  // Why it couldn't be loaded (the bot's words, as codes).
  reasons: {
    plan: "Your plan doesn't allow loading this, or you have no free campaign slot. Look at your plan on My Account. Replacing one of your campaigns doesn't need a free slot.",
    not_allowed: "You are not in that server any more, or you are no longer a DM of that campaign. Pick another server, or load it as a new campaign in one you run.",
    not_found: "The campaign it was to replace is gone. Press Load another and choose A new campaign.",
    playing: "That campaign is playing right now. Stop the session in Discord, then load the copy again.",
    not_a_backup: "That file isn't a DMbot backup. Pick the backup file you downloaded.",
    too_big: `That file is too big. A copy can be up to ${RESTORE_MAX_MB} MB.`,
    parts: "A transcripts file was missing, repeated or from another copy. Load it again with all of its files, or none.",
    stalled: "DMbot hasn't moved for a while. Press Check again, or Load another to start over.",
    failed: "DMbot couldn't load it. Try again; if it fails again, ask on the Help page and say what you were doing. A campaign you were replacing may need a look: say so there.",
  } as Record<string, string>,
  // Said when the API refuses before anything is sent.
  problems: {
    "not-a-backup": "That file isn't a DMbot backup. Pick the file you got from Download a copy.",
    "too-big": `That file is too big. A copy can be up to ${RESTORE_MAX_MB} MB.`,
    "too-many-parts": `This backup has more than ${RESTORE_MAX_PARTS} transcripts files, which is more than can be loaded here. Use /dmbot restore in Discord for this one.`,
    "bad-part": "That transcripts file doesn't belong to this backup. Pick them again, all together from the same download.",
    "files-missing": "Some of the transcripts files haven't arrived. Pick them again, all together from the same download, or press Skip the transcripts.",
    "restore-working": "DMbot has already started loading it, so it can't be taken back. Here is how far it has got.",
    "not-receiving": "This backup has already gone to DMbot. Press Load another to start fresh.",
    "too-soon": "You have loaded several backups in the last hour. Try again in an hour.",
    "slow-down": "You already have a backup on its way. Here it is.",
    "not-found": "We couldn't find that. Pick the file again.",
    "not-allowed": "You can't load a backup into that server. Pick another server, or ask its owner.",
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

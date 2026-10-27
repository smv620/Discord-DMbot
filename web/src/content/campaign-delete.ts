// The words for "Delete this campaign" on My Account, and for a campaign whose Discord server DMbot can no
// longer reach (#2555; core #2554; docs/PLAN.md, "Everything on the website except picking Discord
// channels"). They are Discord's own words (dm_screen/delete_campaign.py) in plain form: the same warning,
// a backup offered first, and the campaign's name typed to confirm. Only the plan holder is offered the
// button; the API checks it again. Shown only while CAMPAIGN_DELETE_LIVE is true; it moves with the
// What's new entry "Delete a campaign from the website".
export const CAMPAIGN_DELETE_LIVE = false;

/** Whether a backup holds a campaign's transcripts yet (core #2552). While it does not, the page says so
 * rather than let someone delete a campaign believing the copy is complete. */
export const BACKUP_HAS_TRANSCRIPTS = true;

export const campaignDeleteWords = {
  /** A campaign on a server DMbot or the person can no longer reach (`serverReachable: false`). */
  unreachable: "DMbot can't reach this server any more. You can still back up or delete the campaign here.",
  /** Its server's name when the API doesn't know it. */
  unknownServer: "Unknown server",
  button: "Delete this campaign",
  buttonFor: (name: string): string => `Delete this campaign: ${name}`,
  heading: (name: string): string => `Delete ${name}?`,
  warning: (name: string): string =>
    `This can't be undone. It deletes ${name} for everyone: story memory, notes, recaps, house rules, homebrew and transcripts.`,
  warningStays: "Copies people already downloaded, and transcripts already posted in your channels, stay.",
  backup: "Download a backup first",
  backingUp: "Getting the backup…",
  // The stages the API names for a copy being made (core progress.py).
  stage: {
    waiting: "Waiting for DMbot to start the backup.",
    reading: "DMbot is reading the campaign.",
    packing: "DMbot is packing the file.",
    fetching: "Getting your backup's files.",
    done: "The backup is ready.",
    failed: "That didn't work.",
  } as Record<string, string>,
  backupNote: "To keep a copy, download it first. It holds everything, secret notes included. Don't share it.",
  backupWithoutTranscripts: "Transcripts aren't in the backup yet, so deleting loses them for good.",
  /** The files unit for the count while they are fetched: "2 of 7 files". */
  filesUnit: "files",
  backupDone: (files: string[]): string =>
    files.length === 1
      ? `Backup downloaded as ${files[0]}. Check your Downloads before you delete.`
      : `Your backup is ${files.length} files. Keep them together: ${files.join(", ")}. If your browser asked, allow the downloads. Check your Downloads before you delete.`,
  /** A file can be bigger than a server without boosts lets you upload in Discord. */
  backupBigFile: "A file in your backup may be bigger than Discord lets a server without boosts upload. If Discord says a file is too big when you restore, ask on the Help page.",
  typeName: "Type the campaign's name to confirm",
  confirm: "Delete it",
  deleting: "Deleting…",
  keep: "Keep it",
  kept: (name: string): string => `OK, ${name} is kept. Nothing was deleted.`,
  typeHint: "Capitals and extra spaces don't matter.",
  noMatch: "Doesn't match yet",
  /** Shown on the list once it is done. */
  deleted: (name: string): string => `Deleted ${name}.`,
  channelsLeft: "Its Discord channels stay until a server admin deletes them.",
  problem: {
    "not-allowed": "Only the game master whose plan this campaign uses can delete it. Ask them to do it.",
    "not-found": "That campaign isn't here any more. The list is up to date now.",
    "session-running": "A session of this campaign is running. End it in Discord, then press Delete it again.",
    "name-mismatch": "That doesn't match the campaign's name, so nothing was deleted. Check the spelling and try again.",
    "no-backup": "Your plan can't make a backup. You can still delete the campaign.",
    "too-big": "This campaign is too big to back up here. Nothing was deleted. Use /dmbot backup in Discord, then try again.",
    "too-soon": "You asked for several backups in the last hour. Try again later. Nothing was deleted.",
    "backup-too-many-files": "This campaign has more transcripts than one backup can carry. Nothing was deleted. Shorten \"Keep transcripts for\" in ⚙️ Settings → 🗂️ Campaign management, then try again.",
    "backup-part": "One of the backup's files didn't come through, so the backup is not complete. Nothing was deleted. Ask for the backup again, and don't rely on the files that did download.",
    "backup-failed": "DMbot couldn't make the backup, and nothing was deleted. Try again; if it fails again, ask on the Help page.",
    network: "We couldn't reach DMbot, so nothing was deleted. Try again in a moment.",
    server: "That didn't work, and nothing was deleted. Try again in a moment.",
  } as Record<string, string>,
};

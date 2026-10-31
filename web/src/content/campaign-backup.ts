// The words for "Keep a copy" on a campaign's page (#3148): one button that downloads a backup of the campaign.
// The wait's own words (stages, files) are shared with the Delete box, in campaign-delete.ts. What is said when a
// backup is saved, slow or fails is different here: nothing is being deleted, and the button is called "Download a
// backup", not "Download a backup first", so the Delete box keeps its own versions and this page has these.
export const campaignBackupWords = {
  heading: "Keep a copy",
  lead: "A backup is one complete copy of this campaign, saved to this device. Keep it somewhere safe.",
  button: "Download a backup",
  /** Said under the button: the file holds the game master's secrets, so players are asked not to open it. */
  note: "This file holds the game master's secrets. Players, please don't open it.",
  done: (files: string[]): string =>
    files.length === 1
      ? `Backup saved: ${files[0]}. Look in your Downloads folder.`
      : `Backup saved: ${files.length} files. Keep them all together. If your browser blocked some, allow downloads and press "Download a backup" again.`,
  bigFile: "A file in your backup is large. If Discord says it is too big when you load the backup, ask on the Help page.",
  fileSlow: "A file is taking a long time. You can wait, or press Download a backup again later.",
  problem: {
    "not-allowed": "Only the campaign's game masters can download a backup here for now. In Discord, type /dmbot backup.",
    "not-found": "That campaign isn't here any more. Go back to My Account to see your campaigns.",
    "no-backup": "This campaign's plan can't make a backup.",
    "too-big": "This campaign is too big to back up here. Use /dmbot backup in Discord instead.",
    "too-soon": "You asked for several backups in the last hour. Try again later.",
    "backup-too-many-files": "This campaign has too many transcripts to back up here. Use /dmbot backup in Discord instead.",
    "backup-part": "One backup file didn't come through, so nothing was saved. Press \"Download a backup\" again.",
    "backup-unfinished": "That backup couldn't be finished. Press \"Download a backup\" again.",
    "backup-failed": "DMbot couldn't make the backup. Try again. If it fails again, ask on the Help page.",
    network: "We couldn't reach DMbot. Try again in a moment.",
    server: "That didn't work. Try again in a moment.",
  } as Record<string, string>,
};

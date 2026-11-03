// The switch of "Remove this speaker's lines" on the Transcripts page, for game masters (#3305 PR 2, #3306). It stays off
// until CloudDev-lead's removal routes and the bot step that applies them are deployed, so the panel never asks the server
// for a list it does not have. Flip it with the What's new entry "Remove a person's lines from a campaign" (docs/GO_LIVE.md).
export const SPEAKER_REMOVAL_LIVE = false;

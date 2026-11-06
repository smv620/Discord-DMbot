// The switch of the "Save the script" button on a session's Transcripts page, for game masters (#3244). It stays off
// until migration 0323 (the DM-only door that reads a session's script) and the script itself (GraphEngineer's
// THEATRE_ON, which makes it) are deployed, so the button never offers a file nothing can make yet. Flip it with the
// What's new entry "Save a session's script" (docs/GO_LIVE.md). On since #3478: both are deployed.
export const THEATRE_DOWNLOAD_LIVE = true;

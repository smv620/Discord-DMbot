// The switch of the Session notes panel on the Transcripts page, for game masters (#3027). It stays off until
// migration 0308 (the two DM-only read doors) is deployed, so the panel never asks the server for notes it does not
// have yet. Flip it with the What's new entry "Session notes on the website" (docs/GO_LIVE.md).
export const SESSION_NOTES_LIVE = false;

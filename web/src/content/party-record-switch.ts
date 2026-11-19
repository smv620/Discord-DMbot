// The party record on a player character's card (#4778 part a2, the page half of #4798): "In the party since…", "Has left the party"
// with why, and "Back in the party", each with Undo. It flips with the party features (`DMBOT_PARTY_JOIN` and the party record's own
// bot half, docs/GO_LIVE.md); until then nothing is drawn and no call is made.
export const PARTY_RECORD_LIVE = false;

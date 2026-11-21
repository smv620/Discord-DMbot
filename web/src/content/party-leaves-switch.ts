// The switch of the "Still in the party?" question on the Questions page (#4895, party knowledge 6a): a character marked dead, or a goodbye
// heard at the table, answered on the page as in Discord's 🧭 card. It stays off until core's read door and answer route are deployed
// (migration 0572, #4898) and dev1 has checked one answer each way and its Undo. Flip it with docs/GO_LIVE.md, "PARTY_LEAVES_LIVE".
export const PARTY_LEAVES_LIVE = false;

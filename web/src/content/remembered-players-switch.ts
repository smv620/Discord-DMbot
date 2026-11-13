// The switch of "People you play with" (#4226): a DM's remembered players with Forget this player, and a player's "DMs who remember
// you" with Clear, the privacy line, and What's new "DMbot remembers your players". It stays off until core's migration 0450 (the
// `dm_players` table and its doors) and the website server are deployed. Flip it with docs/GO_LIVE.md, "REMEMBERED_PLAYERS_LIVE".
export const REMEMBERED_PLAYERS_LIVE = false;

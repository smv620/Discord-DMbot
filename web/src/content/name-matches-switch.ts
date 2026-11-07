// The switch of "DMbot's guesses" on the Names page (#3602): the matches DMbot made by itself, for the DM to
// answer. Off until the website server has the door (core migration 0372, `dmbot_web_waiting_matches`) and the bot
// applies the two answers (`match-confirm`, `match-undo`) and dev1 has seen both work. Flip it with the What's new
// entry "Check DMbot's guesses on the website" (docs/GO_LIVE.md).
export const NAME_MATCHES_LIVE = false;

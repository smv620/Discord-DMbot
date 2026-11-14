// The switch of "Met but not named" on the Names page (#4084, the web half of #4045): the creatures and people the party met at the
// table that nobody named, for the DM to name, say are a name the campaign has, or remove. Off until the website server has the
// door (core migration 0438, `dmbot_web_met_list`, and `met-name`, `met-join`, `met-remove` on the name action queue) and the bot
// applies the three (`web_actions._met`, #4085), and dev1 has checked a sample run with `DMBOT_MET_UNNAMED` on. Flip it with the
// What's new entry "Name the creatures your party met" (docs/GO_LIVE.md).
export const MET_LIST_LIVE = false;

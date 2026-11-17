// The switch of a character's languages on the Names page (#4621, the web half of #4271 part 1): a DM sees a character's languages on its
// row, adds one, marks the ones DMbot found as right, takes one away and undoes the last change, level with Discord's 🗣️ Languages screen.
// Never shown to players. It stays off until core's door and routes (migration 0519, #4608) are deployed and dev1 has checked it.
// Flip it with docs/GO_LIVE.md, "LANGUAGES_LIVE".
export const LANGUAGES_LIVE = false;

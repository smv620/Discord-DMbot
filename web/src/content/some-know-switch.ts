// The switch of the 👥 Some know button on a waiting claim (#4680 part 1, #4717): the DM ticks which characters know a claim, and only
// they know it, with Undo. In a file of its own so the page only asks "is it on?". It stays off until migration 0535 (#4709) and the
// bot's `claim-some` applier are deployed (bot first) and dev1 has answered one claim with 👥 and taken it back; it is flipped with
// docs/GO_LIVE.md, "SOME_KNOW_LIVE".
export const SOME_KNOW_LIVE = false;

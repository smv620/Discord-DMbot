// The switch of the Plot threads panel on the Names page (#3696, slice 1): the campaign's open plot threads, read only, for the DM, level
// with Discord's threads card. It stays off until core's read door and its route are deployed (#3696, with GraphEngineer's count function
// #4909) and dev1 has seen a thread's state, people and history on the site match Discord's card, and a stalled one marked. Flip it with
// docs/GO_LIVE.md, "PLOT_THREADS_LIVE". Not the same switch as THREADS_LIVE (the upload story threads, #4457).
export const PLOT_THREADS_LIVE = false;

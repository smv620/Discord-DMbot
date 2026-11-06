// The switch of the clock offer on an imported session (#3199, #3259). It stays off until CloudDev's import-clock
// row, progress field and queued choice are merged and deployed: bot first, then the switch. Flip it with its
// What's new entry (docs/GO_LIVE.md, "the clock offer").
export const IMPORT_CLOCK_LIVE = false;

// The switch of a place's assumed parent on the Names and Story Graph pages (#4304, website half of #4173 slice 3): the
// "(assumed)" mark on a connection and the DM's ✅ That's right button. In a file of its own so the pages only ask "is it on?".
// Switched off until migration 0471 (the DM's Full View carries the mark, and the `link-right` action) is deployed and dev1
// has checked it; flip it with the What's new entry "DMbot's guess of where a place is" (docs/GO_LIVE.md).
export const ASSUMED_PARENT_LIVE = false;

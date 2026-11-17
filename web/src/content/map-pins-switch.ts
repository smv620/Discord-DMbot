// The switch of the "Pins and scale" panel under each map on the Maps page (#4672, the web half of MapBot slice 4a). A DM reads the
// pins DMbot found on a map, and its scale, grid and north. In a file of its own so the page only asks "is it on?". Switched off until
// core 0530 and 0531 (#4673, #4686) are deployed and dev1 has read a map and seen its pins and scale on the page; flip it with
// docs/GO_LIVE.md, "MAP_PINS_LIVE".
export const MAP_PINS_LIVE = false;

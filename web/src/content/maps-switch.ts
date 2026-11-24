// The switch of the Maps page (#4347, the website half of MapBot slice 3): the maps a campaign has, with a DM's yes or no, show or hide and
// remove, and a poster's take-back. It stays off until core 0487 (the read door and the `maps-*` actions) and the web API's routes are
// deployed and dev1 has answered, shown, removed and undone one map as a DM and seen only the shown ones as a player. Flip it with
// docs/GO_LIVE.md, "MAPS_LIVE".
export const MAPS_LIVE = true;

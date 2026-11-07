// The switch of "What's switched on" in a campaign's settings (#3693), read-only. Off until the bot writes
// its feature list (core features.write_snapshot, CloudDev's half) and the website server is deployed with the
// read-only status volume (docker-compose.yml `bot-status`). Flip it with the What's new entry "See what DMbot has
// switched on" (docs/GO_LIVE.md).
export const FEATURES_LIST_LIVE = false;

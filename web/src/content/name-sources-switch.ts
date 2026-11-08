// The switch of the Names page's "Partial campaign" filter (#3675): names found in chosen sessions or
// lore. Off until core can say, for each name, which sessions and uploaded lore mention it, and can filter
// the list by them (a new web door and `GET /me/campaigns/{ref}/name-sources`). Flip it with the What's new
// entry "Show names from some sessions only" (docs/GO_LIVE.md).
export const NAME_SOURCES_LIVE = false;

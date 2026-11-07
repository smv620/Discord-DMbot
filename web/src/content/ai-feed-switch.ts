// The switch of the live windows on what DMbot asked the AI, on the lore upload and import pages (#3710). Off until
// the website server is deployed with core migration 0374 (`dmbot_web_ai_feed`, DMBOT_WEB_AI_FEED=1) and the bot
// writes the feed (DMBOT_AI_FEED=1, deployed first), and the owner has decided whether customers' DMs get it. Flip it
// with the What's new entry "See what DMbot asks the AI" (docs/GO_LIVE.md).
export const AI_FEED_LIVE = false;

// The switch of the House-rules file panel on a campaign's House rules page, for its game masters (#2985), in a file of
// its own so the page, which only asks "is it on?", does not carry the panel's words. Switched off until the bot applies
// the website's file requests (CloudDev's half) and the website server is deployed with migration 0295 and
// DMBOT_WEB_HOUSE_FILE=1. Flip it with the What's new entry "Connect your house-rules file on the website"
// (docs/GO_LIVE.md).
export const HOUSE_FILE_LIVE = false;

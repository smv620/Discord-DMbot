// The switch of Pause / Unpause on a campaign's page, for its owner (#2985), in a file of its own so the Settings
// group, which only asks "is it on?", does not carry the panel's words. Switched off until the bot applies a `pause`
// choice from the website's settings queue (CloudDev's half) and the website server is deployed with migration 0291
// and DMBOT_WEB_PAUSE=1. Flip it with the What's new entry "Pause a campaign on the website" (docs/GO_LIVE.md).
export const PAUSE_LIVE = false;

// The switch of the Shared story choice on a campaign's page, for its game masters (#2985), in a file of its own so
// the Settings group, which only asks "is it on?", does not carry the panel's words. Switched off until the bot applies
// a `story_mode` choice from the website's settings queue (CloudDev's half) and the website server is deployed with
// migration 0292 and DMBOT_WEB_STORY_MODE=1. Flip it with the What's new entry "Choose how DMbot follows your
// adventure" (docs/GO_LIVE.md).
export const STORY_MODE_LIVE = false;

// The switch of the Main rules panel on a campaign's page, for its game masters (#2985), in a file of its own so the
// Settings group, which only asks "is it on?", does not carry the panel's words. Switched off until the bot applies a
// `rulesets` choice from the website's settings queue (CloudDev's half) and the website server is deployed with migration
// 0297 and DMBOT_WEB_RULESETS=1. Flip it with the What's new entry "Choose your main rules on the website"
// (docs/GO_LIVE.md).
export const RULESETS_LIVE = false;

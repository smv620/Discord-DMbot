// The switch of the D&D Beyond invite panel on a campaign's page, for its game masters (#2985), in a file of its own so
// the Settings group, which only asks "is it on?", does not carry the panel's words. Switched off until the bot applies
// a `dndbeyond_invite` choice from the website's settings queue (CloudDev's half) and the website server is deployed
// with migration 0296 and DMBOT_WEB_DNDBEYOND_INVITE=1. Flip it with the What's new entry "Pass on your campaign invite" (docs/GO_LIVE.md).
export const INVITE_LIVE = false;

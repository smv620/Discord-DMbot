// The switch of the "Share and follow" section on a campaign's House rules page (#2783), in a file of its own so the
// page, which only asks "is it on?", does not carry the section's words. Switched off until the bot applies the sharing
// choices the website queues (CloudDev's half, #2793) and the website server is deployed with the matching change (core
// web/rule_sharing.py, migration 0262, DMBOT_WEB_RULE_SHARING=1). Flip it with the What's new entry "Share and follow
// house rules on the website" (docs/GO_LIVE.md).
export const RULE_SHARING_LIVE = false;

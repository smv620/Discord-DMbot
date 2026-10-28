// The switch of the optional rules section on a campaign's Settings tab (#2753), in a file of its own so the
// Settings tab, which only asks "is it on?", does not carry the section's words. Switched off until the bot
// applies an optional rule chosen on the website (the settings queue's new choice, CloudDev's half) and the
// website server is deployed with the matching change (core web/optional_rules.py, migration 0252). Flip it with
// the What's new entry "Switch optional rules on the website" (docs/GO_LIVE.md).
export const OPTIONAL_RULES_LIVE = false;

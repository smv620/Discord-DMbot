// The switch of the decided pairs of house rules on the House rules page (#3697): which of two rules that disagree wins,
// as the DM decided, with "Make Rule N win" and "Clear this choice". It stays off until core's read and the two new
// sharing choices (#3777, migration 0385) are merged and deployed, DMBOT_WEB_RULE_SHARING is on, and the bot is
// restarted with the change (docs/GO_LIVE.md).
export const RULE_CLASHES_LIVE = false;

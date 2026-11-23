// The "See changes" card under a followed house-rules set on the House rules page, and the "House rule updates" link on the
// Questions page (#4841 C1, the page half of #4845): a newer version of a set the campaign follows, what would change, and when
// DMbot takes it. It stays off until core's `GET /me/campaigns/{ref}/rule-update` route is deployed and dev1 has seen a waiting
// update on a test campaign. Flip it with docs/GO_LIVE.md, "RULE_UPDATES_LIVE".
export const RULE_UPDATES_LIVE = true;

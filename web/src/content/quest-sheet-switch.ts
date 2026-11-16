// The switch of the quest sheet in the Story Graph's detail pane (#4547, the web half of #4540): a DM who selects a story thread sees
// its steps, rewards and people above the pane's buttons. It stays off until core's card route (`GET .../threads/{thread}/card`,
// migrations 0511 and 0512) is deployed and dev1 has clicked the sample's quest and seen the sheet, and a player's view shows none.
// Flip it with docs/GO_LIVE.md, "QUEST_SHEET_LIVE".
export const QUEST_SHEET_LIVE = false;

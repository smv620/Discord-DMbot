// The switch of the short summary of a picked name on the Story Graph (#3236 slice 2, #3243). It stays off until
// the website route (GET .../web/nodes/{id}/summary) and GraphEngineer's builder are merged and deployed, so the
// page shell is never shown with an answer the bot can't give. Flip it with its What's new entry (docs/GO_LIVE.md).
export const NODE_SUMMARY_LIVE = false;

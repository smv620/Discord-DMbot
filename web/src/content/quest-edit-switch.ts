// The switch of the quest sheet's edit controls (#4562, the web half of #4476 slice 2): a DM changes a kept thread's steps, rewards and
// more from the quest sheet, as Discord's 🧵 "📋 Steps, rewards and more" does. It stays off until core's change route
// (`POST .../threads/{thread}/change`, migration 0514) and the answer route for its actions are deployed and dev1 has checked it.
// Flip it with docs/GO_LIVE.md, "QUEST_EDIT_LIVE".
export const QUEST_EDIT_LIVE = false;

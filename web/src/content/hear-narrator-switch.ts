// The switch of 🔊 Hear it in the recap narrator's voice (#3807): the press is queued for the bot, which makes the clip
// with the narrator's voice and speech model (core #3817, migration 0387). It stays off until that is merged and deployed
// and the bot restarted with it, so the button keeps reading the name with the browser's own voice (docs/GO_LIVE.md).
export const HEAR_NARRATOR_LIVE = false;

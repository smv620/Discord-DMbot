// The switch of the game masters section on a campaign's Settings tab (#2768), in a file of its own so the Settings
// tab, which only asks "is it on?", does not carry the section's words. Switched off until the bot applies a game
// master added or removed on the website (the settings queue's `co_dm` choice, CloudDev's half) and the website
// server is deployed with the matching change (core web/game_masters.py, migrations 0257 and the queue's).
// Flip it with the What's new entry "Add and remove game masters on the website" (docs/GO_LIVE.md).
export const CO_DMS_LIVE = false;

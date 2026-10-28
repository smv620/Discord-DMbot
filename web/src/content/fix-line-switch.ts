// The switch of "Fix this line" on a session's Cleaned transcript (#2858), in a file of its own so the Transcripts
// page, which only asks "is it on?", does not carry the words. Switched off until the bot applies the fixes the website
// queues (CloudDev's half, #2883) and the website server runs with DMBOT_WEB_LINE_FIX=1 (the bot goes first). Flip it
// with the What's new entry "Fix a line from the website" (docs/GO_LIVE.md).
export const FIX_LINE_LIVE = false;

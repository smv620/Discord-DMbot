// The switch of "Delete my transcripts" on My Account (#2769), in a file of its own so My Account, which only asks
// "is it on?", does not carry the words. Switched off until the bot runs a deletion asked for on the website (the
// queue `web_line_deletions`, CloudDev's half) and the website server is deployed with the matching change (core
// web/my_lines.py, migration 0260, DMBOT_WEB_LINE_DELETE=1). Flip it with the What's new entry "Delete your
// transcripts from the website" (docs/GO_LIVE.md).
export const DELETE_MY_LINES_LIVE = false;

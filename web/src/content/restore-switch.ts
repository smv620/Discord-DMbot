// The switch and the link of the Load a backup page (#2745), in a file of their own so My Account, which only
// asks "is it on, and what does the link say?", does not carry the whole page's words (account.html is close to
// its weight budget, #2743). The page's words are in restore-words.ts.
//
// Switched off until the bot applies a website restore (#2746) and the website server is deployed with the
// matching change (core web/restores.py, migration 0251). Flip it with the What's new entry "Load a backup from the
// website" (docs/GO_LIVE.md).
export const WEB_RESTORE_LIVE = false;

/** The link on My Account's campaigns. */
export const restoreLink = "Load a backup";

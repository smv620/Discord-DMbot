// The switch of "say it in words" on a connection's panel (#5293, the website half of #2940): the DM writes up to 1,000
// characters about two names, DMbot reads them as connections, and the DM chooses who knows and presses Use this, with one
// Undo. Off until core's doors (#5298, behind DMBOT_WEB_CONNECTION_SAID) are deployed, the bot is deployed first, and dev1 has
// read, used and undone one from the page (docs/GO_LIVE.md). Flip it with the What's new entry "Say a connection in words".
export const CONNECTION_SAID_LIVE = false;

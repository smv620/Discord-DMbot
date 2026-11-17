// The switch of "DMbot adds what it is sure of" on the website (#3879, website half of #3878; core #3887): the counts notice
// after a lore upload or an import, Undo this upload for 7 days, the Names filter that opens on what one upload added, and the
// "Ask me before adding" setting. In a file of its own so the pages only ask "is it on?". Switched off until core #3887 and the
// website's doors for it are deployed; flip it with the What's new entry "DMbot adds what it is sure of" (docs/GO_LIVE.md).
export const AUTO_ADD_LIVE = false;

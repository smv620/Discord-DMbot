// The switch of "Upload a PDF" on the website's Upload lore page (#4762; the website half of Discord's PDF upload): choose a PDF,
// DMbot counts its pages and pictures and says what reading it will cost, then the DM's yes reads it, pictures included. In a file
// of its own so the page only asks "is it on?". Switched off until the bot and the web API carrying it are deployed, and flipped
// in the SAME deploy as DMBOT_PICTURE_READING (Supervisor's ruling on #4762: the page must never promise pictures the bot is not
// reading). See docs/GO_LIVE.md.
export const LORE_PDF_LIVE = false;

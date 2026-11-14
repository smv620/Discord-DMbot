// The switch of the website's search bar (#3855, owner request 2026-11-08): a 🔍 in the header that shows the best-matching page
// at once, matched in the browser with no outside service. In a file of its own so the layout only asks "is it on?". Switched
// off until dev1 has seen it on the test server; flip it with the What's new entry "Search the website" (docs/GO_LIVE.md).
// The short AI answer under it (line 2) comes later, with core #3856, behind its own switch.
export const SEARCH_LIVE = true;

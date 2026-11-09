// The switch of the search bar's short answer (#3855 line 2, core #3856): on Enter or Ask the question goes to DMbot, which
// answers from the website's own public words. In a file of its own so the bar only asks "is it on?". Switched off until core
// #3867 is deployed with DMBOT_SITE_ASK on and Cloudflare's two settings are set; flip it with the What's new entry "Ask the
// website a question" (docs/GO_LIVE.md). Line 1 (the best-matching page) works without it.
export const SEARCH_ASK_LIVE = false;

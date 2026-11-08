// Page tests render pages with a bare Astro container, which has no renderer for the search bar's Preact island (#3855). They
// are about each page's own words, so the header's search bar is left out of them; the bar has its own tests
// (search.test.ts, search-bar.test.tsx) and the build checks (weight, CSP) run with the real switch.
import { vi } from "vitest";

// (`held` is there because test/plain-words.test.ts reads that name off every content file.)
vi.mock("../src/content/search-switch", () => ({ SEARCH_LIVE: false, held: undefined }));

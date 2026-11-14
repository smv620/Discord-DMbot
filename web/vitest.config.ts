/// <reference types="vitest/config" />
import { getViteConfig } from "astro/config";

export default getViteConfig({
  test: {
    include: ["test/**/*.test.{ts,tsx}"],
    // The search bar's island needs a renderer that the pages' bare test containers do not have (#3855).
    setupFiles: ["test/setup-search-off.ts"],
    // The account tests use the pretend API, whose links are same-page ("#demo-…").
    env: { PUBLIC_API_BASE: "mock" },
  },
});

/// <reference types="vitest/config" />
import { getViteConfig } from "astro/config";

export default getViteConfig({
  test: {
    include: ["test/**/*.test.{ts,tsx}"],
    // The account tests use the pretend API, whose links are same-page ("#demo-…").
    env: { PUBLIC_API_BASE: "mock" },
  },
});

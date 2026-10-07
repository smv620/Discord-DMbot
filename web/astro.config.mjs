// @ts-check
import preact from "@astrojs/preact";
import { defineConfig } from "astro/config";

// Static site: every page is built to plain HTML. The signed-in area (/account) talks to
// the web API in core (core/src/dmbot/web/) from the browser; nothing here runs on a server.
// `site` becomes the real address once the owner registers the domain (see README.md).
export default defineConfig({
  output: "static",
  // Preact only where a page needs interactivity: the signed-in area (/account).
  integrations: [preact()],
  trailingSlash: "never",
  build: {
    format: "file",
  },
});

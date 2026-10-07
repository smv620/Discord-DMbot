// @ts-check
import { defineConfig } from "astro/config";

// Static site: every page is built to plain HTML. The signed-in area (/account) talks to
// the web API in core (core/src/dmbot/web/) from the browser; nothing here runs on a server.
// `site` becomes the real address once the owner registers the domain (see README.md).
export default defineConfig({
  output: "static",
  trailingSlash: "never",
  build: {
    format: "file",
  },
});

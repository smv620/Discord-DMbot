// @ts-check
import preact from "@astrojs/preact";
import { defineConfig } from "astro/config";

import { effectiveApiBase } from "./scripts/csp.mjs";

// Static site: every page is built to plain HTML. The signed-in area (/account) talks to
// the web API in core (core/src/dmbot/web/) from the browser; nothing here runs on a server.
// `site` becomes the real address once the owner registers the domain (see README.md).
// The pretend API (PUBLIC_API_BASE=mock) is for previews only. Cloudflare Pages sets
// CF_PAGES_BRANCH; refuse to build the live site (main) with it.
if (process.env.PUBLIC_API_BASE === "mock" && process.env.CF_PAGES_BRANCH === "main") {
  throw new Error("PUBLIC_API_BASE=mock can't be used for the live site (branch main).");
}

// The development branch's build (dev.getdmbot.com, #837) talks to the real API; the pages
// read PUBLIC_API_BASE, so point it at PUBLIC_DEV_API_BASE before Astro loads its env.
if (process.env.CF_PAGES_BRANCH === "development" && !process.env.PUBLIC_DEV_API_BASE?.trim()) {
  // Without it dev.getdmbot.com would quietly ship the pretend API and look like it works.
  throw new Error(
    "The development branch needs PUBLIC_DEV_API_BASE. Add it as a Preview variable in Cloudflare " +
      'Pages (docs/DEPLOY.md, "Put the test website online").',
  );
}
const apiBase = effectiveApiBase(process.env);
if (apiBase !== undefined) process.env.PUBLIC_API_BASE = apiBase;

export default defineConfig({
  output: "static",
  // Preact only where a page needs interactivity: the signed-in area (/account).
  integrations: [preact()],
  trailingSlash: "never",
  // Small stylesheets stay inline ("auto"). "never" was considered (#469): Astro's island
  // loader writes an inline <style> anyway, so the CSP keeps 'unsafe-inline' for styles
  // either way, and external CSS would only add a render-blocking request. Account data is
  // shown as text by Preact (escaped), never as markup or styles.
  build: {
    format: "file",
  },
});

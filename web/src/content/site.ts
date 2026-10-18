// The pages that belong in the sitemap (#1145): every public page. The signed-in area
// (/account), the team's /admin and the 404 page are left out; robots.txt also disallows the
// first two. web/test/meta.test.ts checks this list against the pages that are not noindex.
export const publicPaths = [
  "/",
  "/pricing",
  "/install",
  "/help",
  "/players",
  "/whats-new",
  "/hello",
  "/legal/terms",
  "/legal/privacy",
  "/legal/refunds",
] as const;

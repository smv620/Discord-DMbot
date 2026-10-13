// sitemap.xml, built with the site (#1145): the public pages only, at the configured address.
import type { APIRoute } from "astro";

import { publicPaths } from "../content/site";

export const GET: APIRoute = ({ site }) => {
  const origin = site ?? new URL("https://getdmbot.com");
  const urls = publicPaths
    .map((path) => `  <url><loc>${new URL(path, origin).href}</loc></url>`)
    .join("\n");
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
  return new Response(body, { headers: { "Content-Type": "application/xml" } });
};

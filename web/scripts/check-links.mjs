// After `npm run build`: every internal link and asset in dist/ must point at a real file.
// Catches pages that were renamed or links that break with the static output settings
// (format "file", no trailing slash), which the component tests in test/ can't see.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const dist = new URL("../dist/", import.meta.url).pathname;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? files(full) : [full];
  });
}

function target(path) {
  const clean = decodeURIComponent(path.split(/[?#]/)[0]);
  if (clean === "/") return join(dist, "index.html");
  const file = join(dist, clean);
  return [file, `${file}.html`].find((f) => existsSync(f) && statSync(f).isFile());
}

const broken = [];
for (const page of files(dist).filter((f) => f.endsWith(".html"))) {
  const html = readFileSync(page, "utf8");
  for (const [, link] of html.matchAll(/(?:href|src)="(\/[^"]*)"/g)) {
    if (link.startsWith("//")) continue;
    if (!target(link)) broken.push(`${relative(dist, page)} -> ${link}`);
  }
}

if (broken.length > 0) {
  console.error(`Broken internal links:\n${broken.join("\n")}`);
  process.exit(1);
}
console.log("All internal links point at real files.");

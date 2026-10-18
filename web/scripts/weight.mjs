// How much each built page weighs (#1947): the page's own HTML, plus every local CSS, JS, font and
// image file it loads, counted once. Pure functions over a build folder, so the vitest can feed
// them a made-up one; scripts/check-weight.mjs is the command CI runs after the build.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

export const KB = 1024;

const fontExt = /\.(woff2?|ttf|otf|eot)$/i;
const imageExt = /\.(png|jpe?g|webp|gif|avif|svg|ico)$/i;

/** Every file under a folder, as paths relative to it with "/" separators. */
export function filesUnder(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? filesUnder(join(dir, e.name)).map((f) => `${e.name}/${f}`) : [e.name],
  );
}

/** The addresses a piece of HTML, CSS or JS refers to that are files of this site, not other sites. */
export function localReferences(text, kind) {
  const found = new Set();
  const add = (raw) => {
    const ref = raw.trim().replace(/[?#].*$/, "");
    if (ref === "" || /^([a-z][a-z0-9+.-]*:|\/\/|data:)/i.test(ref)) return;
    found.add(ref);
  };
  if (kind === "html") {
    for (const m of text.matchAll(/<link\b[^>]*>/gi)) {
      const tag = m[0];
      const rel = /\brel=["']?([^"'\s>]+(?:\s+[^"'\s>]+)*)/i.exec(tag)?.[1] ?? "";
      if (/(stylesheet|modulepreload|preload|icon)/i.test(rel)) {
        const href = /\bhref=["']([^"']+)["']/i.exec(tag)?.[1];
        if (href) add(href);
      }
    }
    for (const m of text.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["']/gi)) add(m[1]);
    for (const m of text.matchAll(/<(?:img|source|video|audio)\b[^>]*>/gi)) {
      const tag = m[0];
      for (const a of tag.matchAll(/\b(?:src|poster)=["']([^"']+)["']/gi)) add(a[1]);
      for (const a of tag.matchAll(/\bsrcset=["']([^"']+)["']/gi)) for (const part of a[1].split(",")) add(part.trim().split(/\s+/)[0] ?? "");
    }
    // Astro's island loader names the component and its renderer by address.
    for (const m of text.matchAll(/\b(?:component-url|renderer-url)=["']([^"']+)["']/gi)) add(m[1]);
    for (const m of text.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) add(m[1]); // inline <style>
  } else if (kind === "css") {
    for (const m of text.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) add(m[1]);
  } else if (kind === "js") {
    // Other chunks it imports, by a relative path ("./preact.module.js").
    for (const m of text.matchAll(/(?:import|export)\s*(?:[^"'()]*?\sfrom\s*)?["'](\.{1,2}\/[^"']+)["']/g)) add(m[1]);
    for (const m of text.matchAll(/import\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/g)) add(m[1]);
  }
  return [...found];
}

/** Resolve an address found in `from` (a path inside dist) to a file inside dist, or undefined. */
function resolveInDist(dist, from, ref) {
  const target = ref.startsWith("/") ? resolve(dist, "." + ref) : resolve(dist, dirname(from), ref);
  const inside = target === dist || target.startsWith(dist + sep);
  return inside && existsSync(target) && statSync(target).isFile() ? relative(dist, target).split(sep).join("/") : undefined;
}

/** The HTML file's own size and every local file it loads (following CSS and JS), each counted once. */
export function weighPage(distDir, page) {
  const dist = resolve(distDir); // (without a trailing slash, so "inside dist" compares right)
  const html = readFileSync(join(dist, page), "utf8");
  const seen = new Map(); // path -> bytes
  const alternatives = new Set(); // pictures a stylesheet offers instead of another one
  const queue = localReferences(html, "html").map((ref) => ({ from: page, ref }));
  while (queue.length > 0) {
    const { from, ref } = queue.pop();
    const file = resolveInDist(dist, from, ref);
    if (file === undefined || seen.has(file) || file === page) continue;
    seen.set(file, statSync(join(dist, file)).size);
    const kind = file.endsWith(".css") ? "css" : /\.m?js$/.test(file) ? "js" : undefined;
    if (kind === "css") {
      // A stylesheet's background pictures are alternatives (portrait or landscape, light or dark):
      // only one is fetched, so the largest counts and the others are listed but not added.
      const refs = localReferences(readFileSync(join(dist, file), "utf8"), "css");
      const pictures = refs.map((r) => ({ r, f: resolveInDist(dist, file, r) })).filter((x) => x.f !== undefined && imageExt.test(x.f));
      const biggest = pictures.reduce((a, b) => (statSync(join(dist, b.f)).size > (a ? statSync(join(dist, a.f)).size : -1) ? b : a), undefined);
      for (const { r, f } of pictures) if (f !== biggest?.f) alternatives.add(f);
      for (const r of refs) if (!pictures.some((x) => x.r === r) || r === biggest?.r) queue.push({ from: file, ref: r });
    } else if (kind === "js") {
      for (const next of localReferences(readFileSync(join(dist, file), "utf8"), kind)) queue.push({ from: file, ref: next });
    }
  }
  for (const f of alternatives) if (seen.has(f)) alternatives.delete(f);
  return { page, alternatives, htmlBytes: statSync(join(dist, page)).size, files: seen, totalBytes: statSync(join(dist, page)).size + [...seen.values()].reduce((a, b) => a + b, 0) };
}

/** Every page's weight against the budgets; `problems` names each page that passes one. */
export function checkBuild(distDir, budgets) {
  const dist = resolve(distDir);
  const pages = filesUnder(dist).filter((f) => f.endsWith(".html")).sort();
  const rows = pages.map((page) => weighPage(dist, page));
  const problems = [];
  for (const r of rows) {
    const budget = budgets.pages?.[r.page] ?? budgets;
    const htmlMax = budget.htmlKB * KB;
    const totalMax = budget.totalKB * KB;
    if (r.htmlBytes > htmlMax) problems.push(`${r.page}: the HTML is ${(r.htmlBytes / KB).toFixed(1)} KB, over its ${budget.htmlKB} KB budget`);
    if (r.totalBytes > totalMax) problems.push(`${r.page}: everything it loads is ${(r.totalBytes / KB).toFixed(1)} KB, over its ${budget.totalKB} KB budget`);
  }
  // Pictures: any image in the build over the limit, with the pages that load it or show it as a link preview.
  const images = [];
  for (const file of filesUnder(dist).filter((f) => imageExt.test(f))) {
    const bytes = statSync(join(dist, file)).size;
    if (bytes <= budgets.imageKB * KB) continue;
    const base = file.split("/").pop();
    const using = rows.filter((r) => r.files.has(file) || readFileSync(join(dist, r.page), "utf8").includes(base)).map((r) => r.page);
    images.push({ file, bytes, pages: using });
    problems.push(`${file}: ${(bytes / KB).toFixed(1)} KB, over the ${budgets.imageKB} KB limit for a picture${using.length ? ` (used by ${using.slice(0, 4).join(", ")}${using.length > 4 ? ", …" : ""})` : ""}`);
  }
  const fonts = [...new Set(rows.flatMap((r) => [...r.files.keys()].filter((f) => fontExt.test(f))))].sort();
  return { rows, images, fonts, problems };
}

/** The table CI prints: page, HTML KB, total KB, budget. */
export function table(rows, budgets) {
  const lines = ["page".padEnd(28) + "HTML KB".padStart(9) + "total KB".padStart(10) + "budget KB".padStart(12)];
  for (const r of rows) {
    const b = budgets.pages?.[r.page] ?? budgets;
    lines.push(r.page.padEnd(28) + (r.htmlBytes / KB).toFixed(1).padStart(9) + (r.totalBytes / KB).toFixed(1).padStart(10) + `${b.htmlKB} / ${b.totalKB}`.padStart(12));
  }
  return lines.join("\n");
}

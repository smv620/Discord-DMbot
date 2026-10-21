import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args: ["--no-sandbox"] });
const base = "http://127.0.0.1:4401";
const REF = "200000000000000001.cmp1.1791000000";
const adminIn = async (page) => {
  await page.getByLabel("Email").fill("owner@example.com");
  await page.getByLabel(/password/i).first().fill("anything at all here");
  await page.getByRole("button", { name: /sign in/i }).first().click();
  await page.waitForSelector("text=Free access", { timeout: 8000 });
  await page.waitForTimeout(500);
};
const scen = ["signed-out","no-plan","try-it","table","grace","lapsed","offers","free","free-until","free-paying","down"];
const targets = [
  ...scen.map((x) => [`account ${x}`, `/account?demo=${x}`]),
  ["recaps list", "/account/recaps?demo=table"],
  ["recap page", `/account/recaps/${REF}?demo=table`],
  ["transcripts", "/account/transcripts?demo=table"],
  ["house rules", "/account/house-rules?demo=table"],
  ["homebrew", "/account/homebrew?demo=table"],
  ["admin", "/admin", adminIn],
  ["admin tickets open", "/admin", async (page) => { await adminIn(page); const b = page.getByRole("button", { name: /open|read|show|thread|reply/i }); for (const el of await b.all()) { await el.click().catch(() => {}); } }],
  ["pricing", "/pricing"],
];
const measure = () => {
  const vw = innerWidth;
  const out = { sw: document.documentElement.scrollWidth, vw, over: [], small: [] };
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  for (const el of document.body.querySelectorAll("*")) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) {
      // skip things inside a scroll box of their own
      let p = el.parentElement, boxed = false;
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if ((o === "auto" || o === "scroll") && p.scrollWidth > p.clientWidth) { boxed = true; break; } p = p.parentElement; }
      if (!boxed) out.over.push(`${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""} r=${Math.round(r.right)} "${(el.textContent || "").trim().slice(0, 30)}"`);
    }
  }
  const ctl = document.body.querySelectorAll("button, a.button, input:not([type=hidden]), select, textarea, summary, [role=button]");
  for (const el of ctl) {
    if (!vis(el)) continue;
    let r = el.getBoundingClientRect();
    // a checkbox/radio is tapped through its label
    if (el.matches("input[type=checkbox], input[type=radio]")) { const l = el.closest("label"); if (l) r = l.getBoundingClientRect(); }
    if (r.height < 43.5 || r.width < 43.5) out.small.push(`${el.tagName.toLowerCase()} ${Math.round(r.width)}x${Math.round(r.height)} "${(el.getAttribute("aria-label") || el.textContent || el.getAttribute("type") || "").trim().slice(0, 28)}"`);
  }
  return out;
};
for (const w of [360]) {
  for (const [name, path, act] of targets) {
    const ctx = await browser.newContext({ viewport: { width: w, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    await page.goto(base + path, { waitUntil: "networkidle" });
    try { if (act) await act(page); } catch (e) { console.log("act failed", name, String(e).slice(0, 80)); }
    await page.waitForTimeout(700);
    const m = await page.evaluate(measure);
    console.log(`${w} ${name}: scroll ${m.sw}/${m.vw}${m.sw > m.vw ? "  <-- SIDEWAYS" : ""} | over ${m.over.length} | small ${m.small.length}`);
    for (const o of m.over.slice(0, 5)) console.log("    over:", o);
    for (const s of [...new Set(m.small)].slice(0, 8)) console.log("    small:", s);
    await ctx.close();
  }
}
await browser.close();

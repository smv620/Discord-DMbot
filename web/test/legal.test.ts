// The legal drafts (#433): each has the short version, the draft notice, and facts that
// match the plan file, so the pages and the pricing never disagree.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import facts from "../src/content/plans.json";
import { deletionWarnings, notAffiliated, retentionRows } from "../src/content/legal";
import { byId, formatPeriod } from "../src/content/pricing";
import Privacy from "../src/pages/legal/privacy.astro";
import Refunds from "../src/pages/legal/refunds.astro";
import Terms from "../src/pages/legal/terms.astro";

const pages: [name: string, page: AstroComponentFactory][] = [
  ["terms", Terms],
  ["privacy", Privacy],
  ["refunds", Refunds],
];

const rendered = new Map<string, Document>();
const text = (node: Element | Document | null): string =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

beforeAll(async () => {
  const container = await AstroContainer.create();
  for (const [name, page] of pages) {
    const html = await container.renderToString(page, {
      request: new Request(`https://dmbot.example/legal/${name}`),
    });
    rendered.set(name, parsePage(html));
  }
});

const doc = (name: string): Document => {
  const found = rendered.get(name);
  if (!found) throw new Error(`not rendered: ${name}`);
  return found;
};

describe.each(pages.map(([name]) => [name]))("legal/%s", (name) => {
  it("starts with the short version and says it is a draft", () => {
    const main = doc(name).querySelector("main");
    const short = main?.querySelector(".short");
    expect(text(short?.querySelector("h2") ?? null)).toBe("The short version");
    const lines = [...(short?.querySelectorAll("p") ?? [])].map((p) => text(p));
    expect(lines.join(" ").length).toBeGreaterThan(80);
    for (const line of lines) expect(line.length).toBeGreaterThan(0);
    expect(text(main?.querySelector(".draft") ?? null)).toMatch(/^Draft for review/);
    expect(text(main?.querySelector(".effective") ?? null)).toContain(
      "Effective date: 9 October 2026",
    );
  });

  it("leaves no half-written placeholder", () => {
    const body = text(doc(name).querySelector("main"));
    // Remove well-formed placeholders; no stray braces may be left. Nothing leaked from data.
    expect(body.replace(/\{\{[^{}]+\}\}/g, "")).not.toMatch(/[{}]/);
    expect(body).not.toMatch(/\b(undefined|null|NaN)\b|\[object/);
  });
});

describe("retention and warnings", () => {
  it("lists every plan's retention as on the pricing page", () => {
    const rows = [...doc("privacy").querySelectorAll("tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => text(td)),
    );
    expect(rows).toEqual(retentionRows.map((r) => [r.plan, r.keep]));
    expect(rows).toHaveLength(facts.order.length);
    expect(rows).toEqual([
      ["Try It", "60 days"],
      ["Table", "6 months"],
      ["Two Tables", "1 year"],
      ["Guild", "1 year"],
      ["Pro", "1 year"],
    ]);
    expect(rows[0]).toEqual([byId["try-it"].name, formatPeriod(byId["try-it"].keepAfterLastSession)]);
  });

  it("lists every warning day from the plan file", () => {
    expect(deletionWarnings).toBe("14 days and 3 days");
    for (const day of facts.deletionWarningDaysBefore) expect(deletionWarnings).toContain(`${day} day`);
  });

  it("states the 120 days after a plan stops and the warning days", () => {
    expect(text(doc("privacy").querySelector("main"))).toContain(
      "kept for 120 days. We message the game master on Discord 14 days and 3 days before",
    );
    expect(text(doc("terms").querySelector("main"))).toContain("campaigns are kept for 120 days");
    expect(text(doc("refunds").querySelector("main"))).toContain("kept for 120 days");
  });
});

describe("the facts #433 asks for", () => {
  const privacy = (): string => text(doc("privacy").querySelector("main"));
  const terms = (): string => text(doc("terms").querySelector("main"));

  it("names what we keep and who helps us", () => {
    for (const fact of ["Discord account number", "email", "ids of your Discord servers that use DMbot", "Deepgram", "Anthropic"]) {
      expect(privacy()).toContain(fact);
    }
  });

  it("says voices are not kept and transcripts are visible to the whole server", () => {
    expect(privacy()).toContain("Your voice is not kept.");
    expect(privacy()).toContain("everyone in the Discord server");
  });

  it("sets the minimum age at 13", () => {
    expect(terms()).toContain("at least 13");
    expect(privacy()).toContain("at least 13");
  });

  it("says we are not connected to the publisher, once, on the terms page", () => {
    expect(terms()).toContain(notAffiliated);
    expect(privacy()).not.toContain("Wizards");
    expect(text(doc("refunds").querySelector("main"))).not.toContain("Wizards");
  });

  it("says shared material is the game master's responsibility and gets no legal review", () => {
    expect(terms()).toMatch(/confirm they have the right to use it\. We don't check this/);
  });

  it("describes the three DM-screen settings and that peeks are seen", () => {
    expect(privacy()).toMatch(/players who choose to peek \(the usual setting/);
    expect(privacy()).toContain("the game master can see who peeked");
  });

  it("tells people a failed payment has a short grace period, with no number of days", () => {
    expect(terms()).toContain("there is a short grace period to fix it before the plan stops");
    expect(terms()).toContain("Tap Fix my payment in My Account");
    expect(terms()).not.toMatch(/\d+ days to fix it/);
  });

  it("names Lemon Squeezy as the payment company on every legal page", () => {
    for (const page of ["terms", "privacy", "refunds"]) {
      expect(text(doc(page).querySelector("main"))).toContain("Lemon Squeezy");
    }
    expect(terms()).not.toMatch(/PAYMENT PROVIDER|Paddle/);
  });

  it("uses one sign-in cookie and no tracking", () => {
    expect(privacy()).toMatch(/one cookie, only to keep you signed in/);
    expect(privacy()).toContain("no tracking or ad cookies");
  });

  // Stopping now goes through the ⚙️ Menu (#807, #830): no page may send people to a
  // "Stop recording me" button that isn't there.
  it.each(["terms", "privacy"])("%s: stopping is explained through the ⚙️ Menu", (name) => {
    const body = text(rendered.get(name)?.querySelector("main") ?? rendered.get(name) ?? null);
    const stops = [...body.matchAll(/Stop recording me/g)];
    expect(stops.length).toBeGreaterThan(0);
    for (const stop of stops) {
      expect(body.slice(Math.max(0, stop.index - 80), stop.index)).toContain("⚙️ Menu");
    }
  });

  it("names the State of Ohio, USA, for the law and the courts", () => {
    expect(terms()).toContain("The laws of the State of Ohio, USA, apply to these terms");
    expect(terms()).toContain("heard in the courts of the State of Ohio, USA");
    expect(terms()).not.toContain("governing law and courts");
  });
});

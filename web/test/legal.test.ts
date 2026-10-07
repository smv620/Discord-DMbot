// The legal drafts (#433): each has the short version, the draft notice, and facts that
// match the plan file, so the pages and the pricing never disagree.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import type { AstroComponentFactory } from "astro/runtime/server/index.js";
import { parseHTML } from "linkedom";
import { beforeAll, describe, expect, it } from "vitest";

import { notAffiliated, retentionRows } from "../src/content/legal";
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
    rendered.set(name, parseHTML(html).document);
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
    expect(text(short?.querySelector("p") ?? null).length).toBeGreaterThan(80);
    expect(text(main?.querySelector(".draft") ?? null)).toMatch(/^Draft for review/);
    expect(text(main?.querySelector(".effective") ?? null)).toContain(
      "{{EFFECTIVE DATE: to be set by the owner}}",
    );
  });

  it("leaves no half-written placeholder", () => {
    const body = text(doc(name).querySelector("main"));
    // Every {{ has its }}; nothing like "undefined" leaked from the data.
    expect(body.split("{{").length).toBe(body.split("}}").length);
    expect(body).not.toMatch(/undefined|null|NaN|\[object/);
  });
});

describe("the facts agree with the plan file", () => {
  it("lists every plan's retention as on the pricing page", () => {
    const rows = [...doc("privacy").querySelectorAll("tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => text(td)),
    );
    expect(rows).toEqual(retentionRows.map((r) => [r.plan, r.keep]));
    expect(rows[0]).toEqual([byId["try-it"].name, formatPeriod(byId["try-it"].keepAfterLastSession)]);
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
    for (const fact of ["Discord user id", "email", "ids of the Discord servers", "Deepgram", "Anthropic"]) {
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
  });

  it("says shared material is the game master's responsibility and gets no legal review", () => {
    expect(terms()).toMatch(/confirm they have the right to use it\. We don't check this/);
  });

  it("uses one sign-in cookie and no tracking", () => {
    expect(privacy()).toMatch(/one cookie, only to keep you signed in/);
    expect(privacy()).toContain("no tracking or ad cookies");
  });
});

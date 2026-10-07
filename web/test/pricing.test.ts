// The pricing page (#432): the owner's numbers, and what the page shows.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import {
  extraHours,
  formatPrice,
  headline,
  plans,
  pro,
  questions,
  tryIt,
} from "../src/content/pricing";
import Pricing from "../src/pages/pricing.astro";

describe("the owner's plan table (#432)", () => {
  // These numbers are owner decisions. If this test fails, the change needs the owner's
  // say-so on #432 first.
  it("has the decided prices, hours and campaigns", () => {
    const table = [tryIt, ...plans, pro].map((p) => [
      p.name,
      p.priceCents,
      p.hoursPerMonth,
      p.campaigns,
    ]);
    expect(table).toEqual([
      ["Try It", 0, 8, 1],
      ["Table", 899, 18, 1],
      ["Two Tables", 1799, 43, 2],
      ["Guild", 3499, 87, 5],
      ["Pro", null, 217, 20],
    ]);
    expect([extraHours.priceCents, extraHours.hours]).toEqual([499, 10]);
  });

  it("keeps the owner's headline word for word", () => {
    expect(`${headline.title} ${headline.rest}`).toBe(
      "Pick the hours your table plays. Every plan has everything. Change plans any time.",
    );
  });

  it("marks only Table as the usual pick", () => {
    expect([tryIt, ...plans, pro].filter((p) => p.recommended).map((p) => p.id)).toEqual([
      "table",
    ]);
  });

  it("states every plan rule", () => {
    const text = questions.map((q) => `${q.question} ${q.answer}`).join(" ");
    for (const rule of [
      /listening, from when you start it to when you stop it/,
      /Your hours are for the month/,
      /except backups and downloads/,
      /any time/,
      /The others pause\. Nothing in them is deleted/,
      /60 days on Try It, 6 months on Table, and 1 year on the other plans/,
      /120 days/,
      /Hand over this campaign/,
      /delete it all/,
    ]) {
      expect(text).toMatch(rule);
    }
  });
});

describe("formatPrice", () => {
  it.each([
    [899, "$8.99"],
    [1799, "$17.99"],
    [499, "$4.99"],
    [100, "$1.00"],
  ])("%i cents is %s", (cents, shown) => {
    expect(formatPrice(cents)).toBe(shown);
  });
});

describe("the pricing page", () => {
  let html: string;

  beforeAll(async () => {
    const container = await AstroContainer.create();
    html = await container.renderToString(Pricing, {
      request: new Request("https://dmbot.example/pricing"),
    });
  });

  it("shows the headline as the page heading", () => {
    expect(html).toMatch(/<h1[^>]*>Pick the hours your table plays\.<\/h1>/);
  });

  it("shows three plan cards in the row, Table first and marked", () => {
    const row = html.slice(html.indexOf('class="plans"'), html.indexOf('class="every'));
    expect([...row.matchAll(/data-plan="([^"]+)"/g)].map((m) => m[1])).toEqual([
      "table",
      "two-tables",
      "guild",
    ]);
    expect(row).toContain("Most tables pick this");
  });

  it("puts the hours line directly under each price", () => {
    for (const plan of plans) {
      const card = html.slice(html.indexOf(`data-plan="${plan.id}"`));
      const price = card.indexOf(formatPrice(plan.priceCents ?? 0));
      const hours = card.indexOf(plan.hoursLine);
      expect(price).toBeGreaterThan(-1);
      expect(hours).toBeGreaterThan(price);
      // Nothing but the end of the price line between them.
      expect(card.slice(price, hours)).not.toMatch(/<p class="(campaigns|note)"/);
    }
  });

  it("shows no price for Pro and no button to buy it", () => {
    const card = html.slice(html.indexOf('data-plan="pro"'), html.indexOf('data-plan="extra-hours"'));
    expect(card).toContain("Coming soon");
    expect(card).not.toMatch(/\$\d/);
    expect(card).not.toContain('class="button"');
  });

  it("shows extra hours and the free plan", () => {
    expect(html).toContain("$4.99");
    expect(html).toContain("Try it free");
    expect(html).toContain("No backups or downloads.");
  });

  it("has every plan rule as a question", () => {
    for (const q of questions) {
      expect(html).toContain(`>${q.question.replace("'", "&#39;")}</summary>`);
    }
  });

  it("has no tick-box feature table", () => {
    expect(html).not.toContain("<table");
    expect(html).not.toMatch(/[✓✔✗✘]/);
  });

  it("sends every button to the account page for now", () => {
    const targets = [...html.matchAll(/<a class="button" href="([^"]+)"/g)].map((m) => m[1]);
    expect(targets.length).toBe(4);
    expect(new Set(targets)).toEqual(new Set(["/account"]));
  });
});

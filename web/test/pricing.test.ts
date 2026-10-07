// The pricing page (#432): the owner's numbers, the words built from them, and the page.
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { beforeAll, describe, expect, it } from "vitest";

import { parsePage } from "./dom";

import facts from "../src/content/plans.json";
import {
  byId,
  extraHours,
  formatPeriod,
  formatPrice,
  headline,
  labels,
  plans,
  pro,
  questions,
  tryIt,
  tryItLine,
} from "../src/content/pricing";
import Pricing from "../src/pages/pricing.astro";

describe("the owner's plan facts (#432, #437)", () => {
  // Owner decisions. If this test fails, the change needs the owner's say-so on #432 first.
  it("has the decided prices, hours and campaigns", () => {
    const table = Object.values(byId).map((p) => [
      p.name,
      p.priceCents,
      p.hoursPerMonth,
      p.aboutHoursPerWeek,
      p.campaigns,
    ]);
    expect(table).toEqual([
      ["Try It", 0, 8, null, 1],
      ["Table", 899, 18, 4, 1],
      ["Two Tables", 1799, 43, 10, 2],
      ["Guild", 3499, 87, 20, 5],
      ["Pro", null, 217, 50, 20],
    ]);
    expect([extraHours.priceCents, extraHours.hours]).toEqual([499, 10]);
    expect(byId.table.firstMonthAfterTrialCents).toBe(199);
    expect(tryIt.trialDays).toBe(30);
  });

  it("has the decided retention and backups", () => {
    expect(Object.values(byId).map((p) => [p.id, formatPeriod(p.keepAfterLastSession)])).toEqual([
      ["try-it", "60 days"],
      ["table", "6 months"],
      ["two-tables", "1 year"],
      ["guild", "1 year"],
      ["pro", "1 year"],
    ]);
    expect(formatPeriod(facts.keepAfterPlanStopsPaying as { count: number; unit: "day" })).toBe(
      "120 days",
    );
    expect(facts.deletionWarningDaysBefore).toEqual([14, 3]);
    expect(Object.values(byId).filter((p) => !p.backups).map((p) => p.id)).toEqual(["try-it"]);
  });

  it("keeps the owner's headline word for word", () => {
    expect(`${headline.title} ${headline.rest}`).toBe(
      "Pick the hours your table plays. Every plan has everything. Change plans any time.",
    );
  });

  it("marks only Table as the usual pick", () => {
    expect(Object.values(byId).filter((p) => p.recommended).map((p) => p.id)).toEqual(["table"]);
  });
});

describe("the plan file's shape", () => {
  // plans.json is read with a TypeScript cast (JSON imports can't carry its exact types),
  // so check here that it really has the shape the site and core rely on.
  it("has every field, with the right kinds of values", () => {
    expect(Object.keys(facts).sort()).toEqual(
      [
        "_comment",
        "currency",
        "order",
        "plans",
        "extraHours",
        "paymentGraceDays",
        "keepAfterPlanStopsPaying",
        "deletionWarningDaysBefore",
        "recommended",
      ].sort(),
    );
    expect(Object.keys(facts.plans).sort()).toEqual([...facts.order].sort());
    for (const id of facts.order) {
      const plan = facts.plans[id as keyof typeof facts.plans];
      expect(Object.keys(plan).sort(), id).toEqual(
        [
          "name",
          "priceCents",
          "hoursPerMonth",
          "aboutHoursPerWeek",
          "campaigns",
          "trialDays",
          "backups",
          "keepAfterLastSession",
          "firstMonthAfterTrialCents",
        ].sort(),
      );
      expect(Number.isInteger(plan.hoursPerMonth) && plan.hoursPerMonth > 0, id).toBe(true);
      expect(["day", "month", "year"]).toContain(plan.keepAfterLastSession.unit);
    }
    expect(facts.order).toContain(facts.recommended);
    expect(Number.isInteger(facts.paymentGraceDays)).toBe(true);
    const whole = (n: unknown): boolean => Number.isInteger(n) && (n as number) >= 0;
    const wholeOrNull = (n: unknown): boolean => n === null || whole(n);
    for (const id of facts.order) {
      const p = facts.plans[id as keyof typeof facts.plans];
      expect(wholeOrNull(p.priceCents), `${id} priceCents`).toBe(true);
      expect(whole(p.campaigns) && p.campaigns > 0, `${id} campaigns`).toBe(true);
      expect(wholeOrNull(p.trialDays), `${id} trialDays`).toBe(true);
      expect(wholeOrNull(p.aboutHoursPerWeek), `${id} aboutHoursPerWeek`).toBe(true);
      expect(wholeOrNull(p.firstMonthAfterTrialCents), `${id} firstMonth`).toBe(true);
      expect(typeof p.backups, `${id} backups`).toBe("boolean");
      expect(whole(p.keepAfterLastSession.count), `${id} keep`).toBe(true);
    }
    expect(whole(facts.extraHours.hours) && whole(facts.extraHours.priceCents)).toBe(true);
    expect(whole(facts.keepAfterPlanStopsPaying.count)).toBe(true);
    expect(facts.deletionWarningDaysBefore.every(whole)).toBe(true);
  });
});

describe("the words match the numbers", () => {
  it("builds each hours line from the plan's hours", () => {
    expect(Object.values(byId).map((p) => p.hoursLine)).toEqual([
      "8 hours a month",
      "About 4 hours a week (18 hours a month)",
      "About 10 hours a week (43 hours a month)",
      "About 20 hours a week (87 hours a month)",
      "About 50 hours a week (217 hours a month)",
    ]);
  });

  it("says the owner's Try It and Table notes", () => {
    expect(tryItLine).toBe("8 hours a month, 1 campaign. Free for 30 days. No backups or downloads.");
    expect(byId.table.note).toBe("First month $1.99 after Try It.");
  });

  it("offers no button for Pro and one for every other plan", () => {
    expect(pro.action).toBeNull();
    expect([tryIt, ...plans].every((p) => p.action !== null)).toBe(true);
  });

  it("says 'the other plans' only when they really share one retention period", () => {
    const others = [byId["two-tables"], byId.guild, byId.pro].map((p) =>
      formatPeriod(p.keepAfterLastSession),
    );
    expect(new Set(others).size).toBe(1);
  });

  it("states every plan rule from #432", () => {
    const answer = (q: string): string => {
      const found = questions.find((item) => item.question === q);
      if (!found) throw new Error(`no question ${q}`);
      return found.answer;
    };
    expect(answer("What counts as an hour?")).toMatch(
      /listening, from when you start it to when you stop it/,
    );
    expect(answer("Do unused hours carry over?")).toMatch(/^No\. Your hours are for the month\./);
    expect(answer("What's different between the plans?")).toMatch(
      /^Only the hours and how many campaigns you can run\. The free Try It has no backups or downloads; everything else is the same on every plan/,
    );
    expect(answer("Can I change my plan?")).toMatch(/any time/);
    expect(answer("What if a payment doesn't go through?")).toMatch(/keeps working for 7 days/);
    expect(answer("What if I move to a plan with fewer campaigns?")).toMatch(
      /To play a paused one, move to a bigger plan\.$/,
    );
    expect(answer("What if I move to a plan with fewer campaigns?")).toMatch(
      /Nothing is deleted\. The first campaigns you play after the change keep going.*The rest pause/,
    );
    expect(answer("How long do you keep my campaign?")).toBe(
      "After your last game: 60 days on Try It, 6 months on Table, and 1 year on the other plans. If you stop paying, we keep it for 120 days. We message you on Discord 14 days and 3 days before anything is deleted.",
    );
    expect(answer("Can I give a campaign to someone else?")).toMatch(
      /tap Hand over next to the campaign.*room for it/,
    );
    expect(answer("Can I delete everything?")).toMatch(
      /^Yes, from My account\. Your account and the campaigns you run go straight away\./,
    );
  });
});

describe("formatPrice and formatPeriod", () => {
  it.each([
    [899, "$8.99"],
    [1799, "$17.99"],
    [499, "$4.99"],
    [100, "$1.00"],
  ])("%i cents is %s", (cents, shown) => {
    expect(formatPrice(cents)).toBe(shown);
  });

  it("uses the singular for one", () => {
    expect(formatPeriod({ count: 1, unit: "year" })).toBe("1 year");
    expect(formatPeriod({ count: 6, unit: "month" })).toBe("6 months");
  });
});

describe("the pricing page", () => {
  let document: Document;

  const one = (selector: string, root: ParentNode = document): Element => {
    const found = root.querySelector(selector);
    if (!found) throw new Error(`nothing matches ${selector}`);
    return found;
  };
  const text = (el: Element): string => (el.textContent ?? "").replace(/\s+/g, " ").trim();

  beforeAll(async () => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(Pricing, {
      request: new Request("https://dmbot.example/pricing"),
    });
    document = parsePage(html);
  });

  it("shows the headline as the page heading", () => {
    expect(text(one("h1"))).toBe(headline.title);
  });

  it("shows the three paid plans in the swipe row, Table first and marked", () => {
    const row = one(".plans");
    const cards = [...row.querySelectorAll("[data-plan]")];
    expect(cards.map((c) => c.getAttribute("data-plan"))).toEqual(["table", "two-tables", "guild"]);
    expect(text(one(".badge", one('[data-plan="table"]')))).toBe(labels.recommendedBadge);
    expect(row.querySelectorAll(".badge")).toHaveLength(1);
  });

  it("lets keyboard and screen-reader users reach the swipe row", () => {
    const row = one(".plans");
    expect(row.getAttribute("role")).toBe("region");
    expect(row.getAttribute("tabindex")).toBe("0");
    expect(row.getAttribute("aria-label")).toBe(labels.plansRegion);
  });

  it("puts the hours line directly under each price", () => {
    for (const plan of [...plans, pro]) {
      const price = one(`[data-plan="${plan.id}"] .price`);
      const next = price.nextElementSibling;
      expect(next?.className).toContain("hours");
      expect(text(next as Element)).toBe(plan.hoursLine);
      if (plan.priceCents !== null) expect(text(price)).toContain(formatPrice(plan.priceCents));
    }
  });

  it("shows no price for Pro and no button to buy it", () => {
    const card = one('[data-plan="pro"]');
    expect(text(card)).toContain(labels.comingSoon);
    expect(text(card)).not.toMatch(/\$\d/);
    expect(card.querySelector(".button")).toBeNull();
  });

  it("shows the free plan and extra hours", () => {
    expect(text(one(".try"))).toContain(tryItLine);
    expect(text(one('[data-plan="extra-hours"]'))).toContain("$4.99 for 10 hours");
    expect(text(one('[data-plan="extra-hours"]'))).toContain(extraHours.line);
  });

  it("shows every question with its answer", () => {
    const shown = [...document.querySelectorAll(".faq details")].map((d) => [
      text(one("summary", d)),
      text(one("p", d)),
    ]);
    expect(shown).toEqual(questions.map((q) => [q.question, q.answer]));
  });

  it("has no tick-box feature table", () => {
    expect(document.querySelector("table")).toBeNull();
    expect(text(one("main"))).not.toMatch(/[✓✔✗✘]/);
  });

  it("sends every button to the account page for now (#432)", () => {
    const targets = [...document.querySelectorAll("main .button")].map((a) =>
      a.getAttribute("href"),
    );
    expect(targets).toHaveLength([tryIt, ...plans].length);
    expect(new Set(targets)).toEqual(new Set(["/account"]));
  });
});

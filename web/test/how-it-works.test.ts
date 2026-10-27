// The "How DMbot works" page (#2527): it renders, both charts are static boxes with their steps as an
// ordered list, the tables and rules are there, the two links to ask more work, and nothing is shown as
// live before it is. The words themselves are checked for plain words by plain-words.test.ts.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { beforeAll, describe, expect, it } from "vitest";

import page from "../src/pages/how-it-works.astro";
import { afterChart, brains, duringChart, fourRules, team, whoSees } from "../src/content/how-it-works";
import { entries } from "../src/content/whats-new";
import { parsePage } from "./dom";

let html = "";
let doc: Document;
beforeAll(async () => {
  const container = await AstroContainer.create({ renderers: await loadRenderers([getContainerRenderer()]) });
  html = await container.renderToString(page, { request: new Request("https://dmbot.example/how-it-works") });
  doc = parsePage(html);
});

describe("how DMbot works", () => {
  it("keeps the golden rule and the consent rule in their exact words", () => {
    expect(html).toContain("DMbot helps. The DM decides.");
    expect(html).toContain("Only people who said yes");
  });

  it("shows the four rules and the four tables", () => {
    for (const r of fourRules.rules) expect(html).toContain(r.name);
    for (const g of team.groups) expect(doc.body.textContent).toContain(g.title);
    expect(doc.querySelectorAll("table")).toHaveLength(1); // who gets to see what
    for (const r of whoSees.rows) expect(doc.body.textContent).toContain(r.what);
    for (const i of brains.items) expect(doc.body.textContent).toContain(i.name);
  });

  it("names the writer as RecapBot (the Writer)", () => {
    expect(doc.body.textContent).toMatch(/RecapBot\s*\(the Writer\)/);
  });

  it("does not show a helper as live before it is", () => {
    expect(doc.body.textContent).toContain("Coming later.");
    expect(doc.body.textContent).toContain("Built, and off until it is switched on.");
  });

  it.each([duringChart, afterChart])("has the chart '$heading' as boxes, with its steps as an ordered list", (chart) => {
    const section = doc.getElementById(chart.id + "-h")!.closest("section")!;
    // The boxes are for the eyes only; the list is what a screen reader reads.
    expect(section.querySelector(".chart")!.getAttribute("aria-hidden")).toBe("true");
    const items = [...section.querySelectorAll("details ol > li")].map((li) => li.textContent);
    expect(items).toEqual(chart.steps);
    expect(section.querySelectorAll(".box").length).toBeGreaterThan(5);
  });

  it("ships no chart library to the browser", () => {
    expect(html).not.toMatch(/mermaid/i);
    expect(doc.querySelectorAll("script[src]")).toHaveLength(0);
  });

  it("ends with a link to the Say hello form and the public questions", () => {
    const section = doc.getElementById("more-questions")!.closest("section")!;
    const links = [...section.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/hello", "/pricing#questions"]);
    expect(section.textContent).toContain("don't put private details there");
  });

  it("is linked from the footer, Help and the home page, and has its What's new line live", async () => {
    expect(html).toContain('href="/how-it-works"'); // the footer
    const help = (await import("../src/pages/help.astro?raw")).default as string;
    expect(help).toContain("/how-it-works");
    const home = (await import("../src/pages/index.astro?raw")).default as string;
    expect(home).toContain("/how-it-works");
    expect(entries.some((e) => e.title === "How DMbot works")).toBe(true);
  });
});

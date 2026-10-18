// Links into the Help page (#1946): every section and step has an address, none repeats, none that
// worked before is lost, the headings link to themselves without JavaScript, and Copy link copies.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { copiedText, couldNotCopy, linkTo, wireCopy } from "../scripts/helplinks.mjs";
import { wire } from "../scripts/helpsearch.mjs";
import { anchorAliases, aliasesFor, knownAnchors, sectionIds, stepCounter } from "../src/content/help-anchors";
import { gettingHelp } from "../src/content/help";
import { RECAPS_LIVE } from "../src/content/recap-words";
import help from "../src/pages/help.astro";
import { parsePage } from "./dom";

let html = "";
beforeAll(async () => {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  html = await container.renderToString(help, { request: new Request("https://dmbot.example/help") });
});

describe("addresses on the Help page", () => {
  it("never repeats one", () => {
    const ids = [...parsePage(html).querySelectorAll("[id]")].map((e) => e.id);
    const repeated = ids.filter((id, i) => ids.indexOf(id) !== i);
    expect(repeated).toEqual([]);
  });

  it("keeps every address the page had before, or an alias for it", () => {
    const doc = parsePage(html);
    for (const { id, always } of knownAnchors) {
      if (!always && !RECAPS_LIVE) continue;
      expect(doc.getElementById(id), `#${id} is gone`).not.toBeNull();
    }
  });

  it("answers to a renamed heading's old address (the alias mechanism)", () => {
    // None is needed today; the page draws a span with the old id inside the section it points at.
    for (const [old, now] of Object.entries(anchorAliases)) {
      expect(parsePage(html).getElementById(old), old).not.toBeNull();
      expect(parsePage(html).getElementById(now), now).not.toBeNull();
    }
    expect(aliasesFor("nothing-renamed")).toEqual([]);
  });

  it("gives each section an address from its words, and each step one from its section and place", () => {
    const doc = parsePage(html);
    for (const id of Object.values(sectionIds)) {
      expect(doc.getElementById(id)?.tagName, id).toBe("SECTION");
    }
    expect(sectionIds.gettingHelp).toBe(gettingHelp.id);
    const steps = [...doc.querySelectorAll("main section li")];
    expect(steps.length).toBeGreaterThan(50);
    for (const li of steps) {
      expect(li.id, (li.textContent ?? "").slice(0, 40)).toMatch(/^(game-master|players|account|emails|snags|getting-help)-\d+$/);
    }
    // Numbered from 1 in page order, with no gap, inside each section.
    for (const section of doc.querySelectorAll("main section")) {
      const numbers = [...section.querySelectorAll("li")].map((li) => Number(li.id.split("-").pop()));
      expect(numbers, section.id).toEqual(numbers.map((_, i) => i + 1));
    }
  });

  it("numbers steps in the order they are drawn", () => {
    const next = stepCounter("x");
    expect([next(), next(), next()]).toEqual(["x-1", "x-2", "x-3"]);
  });
});

describe("without JavaScript", () => {
  it("makes each section heading a link to its own section", () => {
    const doc = parsePage(html);
    const sections = [...doc.querySelectorAll("main section")];
    expect(sections.length).toBe(Object.keys(sectionIds).length);
    for (const section of sections) {
      const link = section.querySelector("h2 > a");
      expect(link?.getAttribute("href"), section.id).toBe(`#${section.id}`);
      expect(link?.textContent).toBe(section.querySelector("h2")?.textContent);
    }
  });

  it("keeps the Copy link buttons out of sight, named for what they do", () => {
    const doc = parsePage(html);
    const buttons = [...doc.querySelectorAll("[data-copy-link]")];
    expect(buttons).toHaveLength(Object.keys(sectionIds).length);
    for (const b of buttons) {
      expect(b.hasAttribute("hidden")).toBe(true);
      expect(b.getAttribute("aria-label")).toBe("Copy link to this section");
      expect(doc.getElementById(b.getAttribute("data-copy-link") ?? "")?.tagName).toBe("SECTION");
    }
    const status = doc.querySelector("#help-copy-status");
    expect(status?.getAttribute("role")).toBe("status");
    expect(status?.getAttribute("aria-live")).toBe("polite");
  });
});

describe("Copy link", () => {
  afterEach(() => vi.useRealTimers());
  const loc = { origin: "https://getdmbot.com", pathname: "/help", hash: "" };

  function page() {
    const doc = parsePage(html);
    const root = doc.querySelector("main")!;
    const status = doc.querySelector<HTMLElement>("#help-copy-status")!;
    return { doc, root, status, button: root.querySelector<HTMLButtonElement>('[data-copy-link="game-master"]')! };
  }

  it("builds the full address with the anchor", () => {
    expect(linkTo(loc, "game-master-3")).toBe("https://getdmbot.com/help#game-master-3");
  });

  it("shows the buttons, copies the section's address and says so", async () => {
    vi.useFakeTimers();
    const { root, status, button } = page();
    const writeText = vi.fn().mockResolvedValue(undefined);
    wireCopy(root, status, { clipboard: { writeText }, location: { ...loc } });
    expect(button.hidden).toBe(false);
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(writeText).toHaveBeenCalledWith("https://getdmbot.com/help#game-master");
    expect(status.textContent).toBe(copiedText);
    expect(button.textContent).toBe(copiedText);
    await vi.advanceTimersByTimeAsync(2000);
    expect(button.textContent).toBe("Copy link");
  });

  it("puts the link in the address bar when the clipboard is not allowed", async () => {
    vi.useFakeTimers();
    const { root, status, button } = page();
    const location = { ...loc };
    wireCopy(root, status, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("no")) }, location });
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(location.hash).toBe("game-master");
    expect(status.textContent).toBe(couldNotCopy);
  });
});

describe("a link and the search box together", () => {
  it("clears the highlight when you type, and brings it back with a new link", () => {
    const doc = parsePage(html);
    const form = doc.querySelector<HTMLFormElement>("[data-help-search]")!;
    const main = doc.querySelector("main")!;
    wire(form, main);
    expect(main.hasAttribute("data-quiet")).toBe(false);
    const input = form.querySelector("input")!;
    input.value = "menu";
    input.dispatchEvent(new (doc.defaultView as unknown as { Event: typeof Event }).Event("input", { bubbles: true }));
    expect(main.hasAttribute("data-quiet")).toBe(true);
    doc.defaultView!.dispatchEvent(new (doc.defaultView as unknown as { Event: typeof Event }).Event("hashchange"));
    expect(main.hasAttribute("data-quiet")).toBe(false);
  });
});

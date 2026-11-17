// Links into the Help page (#1946): every section and step has an address, none repeats, none that
// worked before is lost, the headings link to themselves without JavaScript, and Copy link copies.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { copiedText, couldNotCopy, linkTo, wireCopy } from "../scripts/helplinks.mjs";
import { applySearch, wire } from "../scripts/helpsearch.mjs";
import { anchorAliases, aliasesFor, knownAnchors, movedIds, sectionIds, stepCounter } from "../src/content/help-anchors";
import { dmSteps, emailSteps, gettingHelp, quickAnswers, snags } from "../src/content/help";
import { RECAPS_LIVE } from "../src/content/recap-words";
import help from "../src/pages/help.astro";
import questionsPage from "../src/pages/help/questions.astro";
import snagsPage from "../src/pages/help/snags.astro";
import { parsePage } from "./dom";

let html = "";
let questionsHtml = "";
let snagsHtml = "";
beforeAll(async () => {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  html = await container.renderToString(help, { request: new Request("https://dmbot.example/help") });
  questionsHtml = await container.renderToString(questionsPage, { request: new Request("https://dmbot.example/help/questions") });
  snagsHtml = await container.renderToString(snagsPage, { request: new Request("https://dmbot.example/help/snags") });
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
      // The snags section moved to /help/snags (#4235); /help keeps an empty mark under its old id.
      expect(doc.getElementById(id)?.tagName, id).toBe(id === sectionIds.snags ? "SPAN" : "SECTION");
    }
    expect(sectionIds.gettingHelp).toBe(gettingHelp.id);
    const steps = [...doc.querySelectorAll("main section li")];
    expect(steps.length).toBeGreaterThan(50);
    for (const li of steps) {
      expect(li.id, (li.textContent ?? "").slice(0, 40)).toMatch(/^(game-master|players|account|emails|snags|getting-help)-\d+$/);
    }
    // Numbered from 1 in page order, with no gap, inside each section.
    for (const section of doc.querySelectorAll("main section")) {
      let numbers = [...section.querySelectorAll("li")].map((li) => Number(li.id.split("-").pop()));
      // The recaps list counts on from where the quick answers (now on their own page) end.
      if (section.id === sectionIds.gameMaster) numbers = numbers.slice(0, dmSteps.length);
      expect(numbers, section.id).toEqual(numbers.map((_, i) => i + 1));
    }
  });

  it("keeps a few step addresses on the same words, so a step put in front of them shows up here", () => {
    const doc = parsePage(html);
    expect(doc.getElementById("game-master-1")?.textContent).toContain("Add DMbot to your Discord server");
    expect(doc.getElementById("players-1")?.textContent).toBeTruthy();
    expect(doc.getElementById("emails-1")?.textContent).toContain(emailSteps[0]!.text.slice(0, 30));
    expect(parsePage(snagsHtml).getElementById("snags-1")?.textContent).toContain(snags[0]!.question);
    expect(parsePage(questionsHtml).getElementById(`game-master-${dmSteps.length + 1}`)?.textContent).toContain(quickAnswers[0]!.text.slice(0, 30));
  });

  it("answers an old address of a moved step on /help, and the step is on exactly one new page (#4235)", () => {
    const doc = parsePage(html);
    const pages = { "/help/questions": parsePage(questionsHtml), "/help/snags": parsePage(snagsHtml) };
    const moved = movedIds(dmSteps.length, quickAnswers.length, snags.length);
    expect(moved.length).toBe(quickAnswers.length + snags.length + 3);
    for (const { id, page } of moved) {
      const mark = doc.getElementById(id);
      expect(mark?.getAttribute("data-moved-to"), `#${id} on /help`).toBe(page);
      // The step itself is on its new page and nowhere else.
      const where = Object.entries(pages).filter(([, d]) => d.getElementById(id) !== null).map(([p]) => p);
      expect(where, `#${id}`).toEqual([page]);
    }
  });

  it("keeps the new pages free of repeated addresses", () => {
    for (const h of [questionsHtml, snagsHtml]) {
      const ids = [...parsePage(h).querySelectorAll("[id]")].map((e) => e.id);
      expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([]);
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
    expect(sections.length).toBe(Object.keys(sectionIds).length - 1); // (the snags moved to their own page)
    for (const section of sections) {
      const link = section.querySelector("h2 > a");
      expect(link?.getAttribute("href"), section.id).toBe(`#${section.id}`);
      expect(link?.textContent).toBe(section.querySelector("h2")?.textContent);
    }
  });

  it("keeps the Copy link buttons out of sight, named for what they do", () => {
    const doc = parsePage(html);
    const buttons = [...doc.querySelectorAll("[data-copy-link]")];
    expect(buttons).toHaveLength(Object.keys(sectionIds).length - 1);
    for (const b of buttons) {
      expect(b.hasAttribute("hidden")).toBe(true);
      expect(b.getAttribute("aria-label")).toMatch(/^Copy link to this section: \S/);
      expect(doc.getElementById(b.getAttribute("data-copy-link") ?? "")?.tagName).toBe("SECTION");
    }
    // Each says which section, so a list of the page's buttons can tell them apart.
    expect(new Set(buttons.map((b) => b.getAttribute("aria-label"))).size).toBe(buttons.length);
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
    expect(button.getAttribute("aria-label")).toBe(copiedText); // the spoken name holds the visible words
    await vi.advanceTimersByTimeAsync(2000);
    expect(button.textContent).toBe("Copy link");
    expect(button.getAttribute("aria-label")).toMatch(/^Copy link to this section: /);
    expect(status.textContent).toBe(""); // nothing stale left in the reading order
  });

  it("announces a second press too", async () => {
    vi.useFakeTimers();
    const { root, status, button } = page();
    wireCopy(root, status, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) }, location: { ...loc } });
    const spoken: string[] = [];
    new (status.ownerDocument.defaultView as unknown as { MutationObserver: typeof MutationObserver }).MutationObserver(() => spoken.push(status.textContent ?? "")).observe(status, { childList: true, characterData: true });
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(status.textContent).toBe(copiedText);
    expect(spoken.filter((t) => t === copiedText).length).toBe(2);
  });

  it("says so when there is no clipboard at all", async () => {
    vi.useFakeTimers();
    const { root, status, button } = page();
    const location = { ...loc };
    wireCopy(root, status, { clipboard: undefined as never, location });
    button.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(status.textContent).toBe(couldNotCopy);
    expect(location.hash).toBe("game-master");
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

  it("brings the highlight back when a link to the same place is clicked", () => {
    const doc = parsePage(html);
    const form = doc.querySelector<HTMLFormElement>("[data-help-search]")!;
    const main = doc.querySelector("main")!;
    wire(form, main);
    main.setAttribute("data-quiet", "");
    doc.querySelector<HTMLElement>('main section > h2 > a[href="#players"]')!.click();
    expect(main.hasAttribute("data-quiet")).toBe(false);
  });

  it("keeps a section's Copy link button and heading while its steps are searched", () => {
    const doc = parsePage(html);
    const main = doc.querySelector("main")!;
    applySearch(main, "stop recording");
    const section = doc.getElementById("players") ?? doc.getElementById("game-master")!;
    const visible = [...main.querySelectorAll("section")].filter((s) => !s.hasAttribute("hidden"));
    expect(visible.length).toBeGreaterThan(0);
    for (const s of visible) {
      expect(s.querySelector("h2")?.hasAttribute("hidden")).toBe(false);
      expect(s.querySelector("[data-copy-link]")?.hasAttribute("data-search-hid")).toBe(false);
    }
    expect(section).not.toBeNull();
  });
});

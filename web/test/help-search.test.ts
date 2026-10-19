// The Help page's search box (#1939): the matcher, what it hides and shows on the real page, and
// that the page without JavaScript is the whole long page it was.
import { getContainerRenderer } from "@astrojs/preact/container-renderer";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { loadRenderers } from "astro:container";
import { beforeAll, describe, expect, it } from "vitest";

import { applySearch, matches, normalize, nothingFound, statusText, wire } from "../scripts/helpsearch.mjs";
import { afterEach, vi } from "vitest";
import help from "../src/pages/help.astro";
import { ruleKindSteps, ruleKindDmSteps, sidebarSourceSteps, accountSteps, dmSteps, playerSteps } from "../src/content/help";
import { parsePage } from "./dom";

describe("matcher", () => {
  it("ignores case", () => {
    expect(matches("STOP Recording", "Stop recording me")).toBe(true);
    expect(matches("stop recording", "STOP RECORDING ME")).toBe(true);
  });

  it("ignores emoji and punctuation", () => {
    expect(normalize("🗂️ Menu, then 🗑 Delete my transcripts!")).toBe("menu then delete my transcripts");
    expect(matches("menu delete", "press 🗂️ Menu, then 🗑 Delete my transcripts")).toBe(true);
    expect(matches("🗑", "anything")).toBe(true); // an emoji alone is no word
  });

  it("finds accented words without the accent, and keeps an apostrophe inside its word", () => {
    expect(matches("cafe", "A caf\u00e9 near the table")).toBe(true);
    expect(matches("cafe", "caf\u0065\u0301")).toBe(true); // the accent typed as a separate mark
    expect(normalize("don't")).toBe("dont");
    expect(normalize("don\u2019t")).toBe("dont");
    expect(matches("dont", "Please don't stop")).toBe(true);
    expect(matches("don't", "Please don\u2019t stop")).toBe(true);
  });

  it("finds a button name inside a step", () => {
    expect(matches("host the party", "Press ➕ New campaign, then Host the party")).toBe(true);
  });

  it("wants every word, in any order", () => {
    expect(matches("recording stop", "stop recording me")).toBe(true);
    expect(matches("stop elephants", "stop recording me")).toBe(false);
  });

  it("matches everything for an empty box, and nothing for a word that isn't there", () => {
    expect(matches("", "x")).toBe(true);
    expect(matches("   ", "x")).toBe(true);
    expect(matches("zzzq", "stop recording me")).toBe(false);
  });

  it("says the count, or what to do", () => {
    expect(statusText("menu", 4)).toBe("4 steps found");
    expect(statusText("menu", 1)).toBe("1 step found");
    expect(statusText("zzzq", 0)).toBe(nothingFound);
    expect(nothingFound).toBe("Nothing found. Try another word, or see all steps.");
    expect(statusText("", 0)).toBe("");
  });
});

let html = "";
beforeAll(async () => {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  html = await container.renderToString(help, { request: new Request("https://dmbot.example/help") });
});

const visibleSteps = (doc: Document): string[] =>
  [...doc.querySelectorAll("main section li")].filter((li) => !li.closest("[hidden]") && !li.hasAttribute("hidden")).map((li) => li.textContent ?? "");

describe("on the Help page", () => {
  it("finds the Menu step for 'stop recording'", () => {
    const doc = parsePage(html);
    const found = applySearch(doc.querySelector("main")!, "stop recording");
    const shown = visibleSteps(doc);
    expect(found).toBe(shown.length);
    expect(found).toBeGreaterThan(0);
    expect(shown.some((t) => t.includes("🗂️ Menu") && t.includes("Stop recording me"))).toBe(true);
  });

  it("keeps matching steps under their section headings, and hides the rest", () => {
    const doc = parsePage(html);
    applySearch(doc.querySelector("main")!, "stop recording");
    const sections = [...doc.querySelectorAll("main section")];
    const shown = sections.filter((s) => !s.hasAttribute("hidden"));
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.length).toBeLessThan(sections.length);
    for (const s of shown) expect(s.querySelector("h2")?.hasAttribute("hidden")).toBe(false);
  });

  it("finds a step by a button name, ignoring the emoji in front of it", () => {
    const doc = parsePage(html);
    expect(applySearch(doc.querySelector("main")!, "delete my transcripts")).toBeGreaterThan(0);
  });

  it("shows nothing for a word that isn't there, and puts everything back when cleared", () => {
    const doc = parsePage(html);
    const before = visibleSteps(doc).length;
    const main = doc.querySelector("main")!;
    expect(applySearch(main, "zzzqxy")).toBe(0);
    expect(visibleSteps(doc)).toEqual([]);
    expect(applySearch(main, "")).toBe(0);
    expect(visibleSteps(doc)).toHaveLength(before);
    expect(doc.querySelectorAll("[data-search-hid]")).toHaveLength(0);
  });

  it("leaves the contents list alone", () => {
    const doc = parsePage(html);
    applySearch(doc.querySelector("main")!, "zzzqxy");
    expect(doc.querySelector("nav")?.hasAttribute("hidden")).toBe(false);
  });
});

describe("without JavaScript", () => {
  it("shows every step, and keeps the search box out of sight", () => {
    const doc = parsePage(html);
    // Nothing is hidden until the script runs, except the box that needs the script.
    const hidden = [...doc.querySelectorAll("[hidden]")];
    expect(hidden.map((e) => e.hasAttribute("data-help-search") || e.hasAttribute("data-help-search-clear") || e.hasAttribute("data-copy-link"))).toEqual(hidden.map(() => true));
    const text = doc.querySelector("main")!.textContent ?? "";
    for (const step of [...dmSteps, ...playerSteps, ...accountSteps]) {
      if (step.text.startsWith("Add DMbot") || step.text.startsWith("Forgot") || step.text.includes("your account page")) continue; // the page words these around a link
      expect(text.replace(/\s+/g, " ")).toContain(step.text.replace(/\s+/g, " ").slice(0, 60));
    }
  });

  it("labels the box, announces results politely, and sends nothing", () => {
    const doc = parsePage(html);
    const label = doc.querySelector('label[for="help-search"]');
    expect(label?.textContent).toBe("Search help");
    expect(doc.querySelector("#help-search")?.getAttribute("type")).toBe("search");
    const status = doc.querySelector("#help-search-status");
    expect(status?.getAttribute("role")).toBe("status");
    expect(status?.getAttribute("aria-live")).toBe("polite");
    // A form with no action and no method: nothing in the markup can send what was typed.
    const form = doc.querySelector("[data-help-search]")!;
    expect(form.getAttribute("role")).toBe("search");
    expect(form.hasAttribute("action")).toBe(false);
  });
});

describe("on the Help page, more", () => {
  it("hides a list whose steps don't match but keeps the heading of the list that does", () => {
    const doc = parsePage(html);
    const main = doc.querySelector("main")!;
    // Quick answers sit under their own heading inside the game master section.
    const quick = doc.querySelector("#quick-heading")!;
    const list = quick.nextElementSibling!;
    const first = list.querySelector("li")!.textContent ?? "";
    // A word that is only in that quick answer, and nowhere in the steps before it.
    const word = normalize(first).split(" ").find((w) => w.length > 6 && !(doc.querySelector("#dm-heading ~ ol")?.textContent ?? "").toLowerCase().includes(w));
    expect(word, "a word only the quick answer has").toBeTruthy();
    applySearch(main, word!);
    expect(quick.hasAttribute("hidden")).toBe(false);
    expect(list.hasAttribute("hidden")).toBe(false);
    expect(doc.querySelector("#dm-heading ~ ol")?.hasAttribute("hidden")).toBe(true);
  });

  it("hides the contents links to sections it hides", () => {
    const doc = parsePage(html);
    applySearch(doc.querySelector("main")!, "stop recording");
    const links = [...doc.querySelectorAll('nav a[href^="#"]')];
    const hiddenLinks = links.filter((a) => a.closest("li")?.hasAttribute("hidden"));
    expect(hiddenLinks.length).toBeGreaterThan(0);
    expect(hiddenLinks.length).toBeLessThan(links.length);
    applySearch(doc.querySelector("main")!, "");
    expect(doc.querySelectorAll("nav [hidden]")).toHaveLength(0);
  });
});

describe("the box, wired up", () => {
  afterEach(() => vi.useRealTimers());

  function page() {
    const doc = parsePage(html);
    const form = doc.querySelector<HTMLFormElement>("[data-help-search]")!;
    const main = doc.querySelector("main")!;
    const input = form.querySelector("input")!;
    const status = form.querySelector("#help-search-status")!;
    const clear = form.querySelector<HTMLButtonElement>("[data-help-search-clear]")!;
    return { doc, form, main, input, status, clear };
  }
  const type = (input: HTMLInputElement, value: string) => {
    input.value = value;
    input.dispatchEvent(new input.ownerDocument.defaultView!.Event("input", { bubbles: true }));
  };

  it("shows the box, filters as you type, and says the count a moment after the last key", () => {
    vi.useFakeTimers();
    const { form, main, input, status } = page();
    wire(form, main);
    expect(form.hidden).toBe(false);
    type(input, "menu");
    type(input, "menu delete");
    expect(status.textContent).toBe(""); // not yet: it waits for a pause
    vi.advanceTimersByTime(300);
    expect(status.textContent).toMatch(/^\d+ steps? found$/);
  });

  it("says nothing was found, shows the button, and the button clears the box and puts focus back", () => {
    vi.useFakeTimers();
    const { doc, form, main, input, status, clear } = page();
    wire(form, main);
    type(input, "zzzqxy");
    vi.advanceTimersByTime(300);
    expect(status.textContent).toBe(nothingFound);
    expect(clear.hidden).toBe(false);
    clear.click();
    expect(input.value).toBe("");
    expect(status.textContent).toBe("");
    expect(clear.hidden).toBe(true);
    expect(doc.querySelectorAll("[data-search-hid]")).toHaveLength(0);
  });

  it("clears on Esc, and Enter submits nothing", () => {
    vi.useFakeTimers();
    const { doc, form, main, input, status } = page();
    wire(form, main);
    type(input, "menu");
    const Window = doc.defaultView!;
    input.dispatchEvent(new Window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(input.value).toBe("");
    expect(status.textContent).toBe("");
    expect(doc.querySelectorAll("[data-search-hid]")).toHaveLength(0);
    const submit = new Window.Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(submit);
    expect(submit.defaultPrevented).toBe(true);
  });
});

describe("Help search finds the 2014 mark and the game master's entries (#1961)", () => {
  it("matches legacy, 2014, trap and poison", () => {
    const text = [...ruleKindSteps, ...ruleKindDmSteps].map((s) => s.text).join(" ");
    for (const query of ["legacy", "2014", "trap", "poison"]) {
      expect(matches(query, text), query).toBe(true);
    }
  });
});

describe("Help search finds the sources step (#1958)", () => {
  it("matches the three ways a game master would ask", () => {
    // Wording only: the step is held, so this does not look at the rendered page.
    expect(sidebarSourceSteps.length).toBeGreaterThan(0);
    const text = sidebarSourceSteps.map((s) => s.text).join(" ");
    for (const query of ["where did that come from", "source", "page number"]) {
      expect(matches(query, text), query).toBe(true);
    }
  });
});

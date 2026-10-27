/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Where it was said" under a name to check (#2403; core #2329). Shown only while NAME_PLACES_LIVE is true
// (false today), so these tests switch it on. The lines are transcript words: they are drawn, never logged.
import { cleanup, render, screen, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameCheckWords as nw } from "../src/content/recap-name-checks";
import { namePlaceWords as w } from "../src/content/name-places";

const live = vi.hoisted(() => ({ places: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return true;
    },
    get RECAPS_LIVE() {
      return true;
    },
    get NAME_PLACES_LIVE() {
      return live.places;
    },
  };
});

afterEach(() => {
  cleanup();
  live.places = false;
  vi.restoreAllMocks();
});

const draft = mockRecaps()[0]!;
const row = (name: string) => screen.getByRole("group", { name }) as HTMLElement;

async function open(on = true) {
  live.places = on;
  render(<RecapNameChecks api={mockApi("table")} refId={draft.ref} />);
  await screen.findByRole("heading", { name: nw.heading });
}

describe("where it was said", () => {
  it("is not shown while it is switched off", async () => {
    await open(false);
    expect(screen.queryByText(w.heading)).toBeNull();
  });

  it("draws each window with its speaker, time and words, and a placeholder for a removed line", async () => {
    await open();
    const card = within(row("Brannoch"));
    expect(card.getByText(w.heading)).toBeTruthy();
    expect(card.getByText(w.session(12))).toBeTruthy();
    expect(card.getByText(/we rode to Brannoch's forge/)).toBeTruthy();
    expect(card.getByText(`Mira ${w.time(1840)}`)).toBeTruthy();
    expect(card.getByText(w.lineRemoved)).toBeTruthy();
  });

  it("says so when a whole window is no longer available", async () => {
    await open();
    expect(within(row("Brannoch")).getByText(w.gone(9))).toBeTruthy();
  });

  it("links to the transcript at that session and counts the places left out", async () => {
    await open();
    const card = within(row("Brannoch"));
    const link = card.getByRole("link", { name: w.openTranscriptFor(12) }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toMatch(/^\/account\/transcripts\/[^/]+\/12$/);
    expect(card.getByText(w.more(3))).toBeTruthy();
  });

  it("shows nothing for a name with no places", async () => {
    await open();
    expect(within(row("Garrick")).queryByText(w.heading)).toBeNull();
  });

  it("formats times as m:ss and h:mm:ss", () => {
    expect(w.time(65)).toBe("1:05");
    expect(w.time(3725)).toBe("1:02:05");
    expect(w.time(-4)).toBe("0:00");
    expect(w.more(1)).toBe("1 more place in the transcripts.");
  });

  it("never writes the transcript words to the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    await open();
    for (const s of spies) for (const call of s.mock.calls) expect(JSON.stringify(call)).not.toContain("we rode to Brannoch");
  });

  it("has no accessibility violations", async () => {
    await open();
    const result = await axe.run(document.body);
    expect(result.violations).toEqual([]);
  });
});

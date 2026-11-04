/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Where it was said" under a name to check (#2403; core #2329). Shown only while NAME_PLACES_LIVE is true
// (false today), so these tests switch it on. The lines are transcript words: they are drawn, never logged.
import { readFileSync } from "node:fs";

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import NamePlaces from "../src/account/NamePlaces";
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
const row = (name: string) =>
  screen.getByRole("group", { name }) as HTMLElement;

/** Press a name's "📜 Where was it said?" button, which opens its places. */
function show(name: string) {
  fireEvent.click(within(row(name)).getByRole("button", { name: w.button }));
}

async function open(on = true) {
  live.places = on;
  render(<RecapNameChecks api={mockApi("table")} refId={draft.ref} />);
  await screen.findByRole("heading", { name: nw.heading });
}

describe("where it was said", () => {
  it("is not shown while it is switched off", async () => {
    await open(false);
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("draws each window with its speaker, time and words, and a placeholder for a removed line", async () => {
    await open();
    const card = within(row("Brannoch"));
    // the newest place is there with no click (#3384); the rest wait behind the button
    expect(card.getByText(w.session(12))).toBeTruthy();
    expect(card.getByText(/we rode to Brannoch's forge/)).toBeTruthy();
    expect(card.getByText(`Mira ${w.time(1840)}`)).toBeTruthy();
    expect(card.getByText(w.lineRemoved)).toBeTruthy();
  });

  it("says so when a whole window is no longer available", async () => {
    await open();
    expect(within(row("Brannoch")).queryByText(w.gone(9))).toBeNull(); // the older place is behind the button
    show("Brannoch");
    expect(within(row("Brannoch")).getByText(w.gone(9))).toBeTruthy();
  });

  it("links to the transcript at that session and counts the places left out", async () => {
    await open();
    show("Brannoch");
    const card = within(row("Brannoch"));
    const link = card.getByRole("link", {
      name: w.openTranscriptFor(12),
    }) as HTMLAnchorElement;
    const campaign = draft.ref.split(".").slice(0, 2).join(".");
    expect(link.getAttribute("href")).toBe(
      `/account/transcripts/${encodeURIComponent(campaign)}/12`,
    );
    expect(card.getByText(w.heard(5, 2))).toBeTruthy();
  });

  it("shows nothing for a name with no places", async () => {
    await open();
    expect(
      within(row("Garrick")).queryByRole("button", { name: w.button }),
    ).toBeNull();
  });

  it("formats times as m:ss and h:mm:ss", () => {
    expect(w.time(65)).toBe("at 1:05");
    expect(w.time(3725)).toBe("at 1:02:05");
    expect(w.time(-4)).toBe("at 0:00");
    expect(w.heard(5, 3)).toBe("Heard 5 times. Showing 3.");
  });

  it("never writes the transcript words to the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map(
      (m) => vi.spyOn(console, m).mockImplementation(() => {}),
    );
    await open();
    for (const s of spies)
      for (const call of s.mock.calls)
        expect(JSON.stringify(call)).not.toContain("we rode to Brannoch");
  });

  it("has no accessibility violations", async () => {
    await open();
    show("Brannoch");
    const result = await axe.run(document.body);
    expect(result.violations).toEqual([]);
  });
});

describe("the page itself", () => {
  it("never draws a removed line's words or speaker, nor lines of an unavailable window", () => {
    const places = [
      {
        session: 3,
        available: true,
        lines: [{ removed: true, speaker: "Mira", at: 5, text: "SECRET" }],
      },
      {
        session: 4,
        available: false,
        lines: [{ speaker: "Kes", at: 9, text: "HIDDEN" }],
      },
    ] as unknown as Parameters<typeof NamePlaces>[0]["places"];
    const { container } = render(
      <NamePlaces places={places} more={0} campaignRef="1.2" />,
    );
    const button = screen.queryByRole("button", { name: w.button });
    if (button) fireEvent.click(button);
    const text = container.textContent ?? "";
    for (const bad of ["SECRET", "Mira", "HIDDEN", "Kes"])
      expect(text).not.toContain(bad);
    expect(text).toContain(w.lineRemoved);
    expect(text).toContain(w.gone(4));
  });

  it("with no windows but more places, the button opens to the count alone", () => {
    render(<NamePlaces places={[]} more={4} campaignRef="1.2" />);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(screen.getByText(w.heard(4, 0))).toBeTruthy();
  });

  it("draws the newest place with no click, and the other two only after the button is pressed", () => {
    const lines = [1, 2, 3, 4].map((n) => ({
      speaker: "Mira",
      at: n,
      text: `line${n}`,
    }));
    const places = [4, 3, 2, 1].map((session) => ({
      session,
      available: true,
      lines,
    }));
    const { container } = render(
      <NamePlaces places={places} more={0} campaignRef="1.2" />,
    );
    // closed: only the newest window (session 4, three of its lines)
    expect(container.textContent).toContain(w.session(4));
    expect(container.textContent).not.toContain(w.session(3));
    expect(container.querySelectorAll("li li")).toHaveLength(3);
    const button = screen.getByRole("button", { name: w.button });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).not.toContain("line4"); // three lines to a window
    expect(container.textContent).toContain(w.session(3));
    expect(container.textContent).toContain(w.session(2));
    expect(container.textContent).not.toContain(w.session(1)); // three places at most
    expect(container.querySelectorAll("li li")).toHaveLength(9);
  });

  it("has no button when the newest place is the only one", () => {
    const lines = [{ speaker: "Mira", at: 5, text: "just once" }];
    const { container } = render(
      <NamePlaces
        places={[{ session: 7, available: true, lines }]}
        more={0}
        campaignRef="1.2"
      />,
    );
    expect(container.textContent).toContain("just once");
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("is held with its What's new entry and one GO_LIVE row", async () => {
    const real = await vi.importActual<
      typeof import("../src/content/recap-words")
    >("../src/content/recap-words");
    const { held } = await import("../src/content/whats-new");
    expect(
      held
        .map((h) => h.title)
        .includes("See where a recap's new name was said"),
    ).toBe(!real.NAME_PLACES_LIVE);
    const golive = readFileSync(process.cwd() + "/../docs/GO_LIVE.md", "utf8");
    expect(
      golive.split("\n").filter((l) => l.startsWith("| `NAME_PLACES_LIVE`")),
    ).toHaveLength(1);
  });
});

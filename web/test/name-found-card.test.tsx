/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The new name card on the website (#5144, core #5182): how a name was heard, DMbot's guess, the line that made it a name and
// "Yes, spelled X". Shown only while NAME_FOUND_LIVE is true (false today), so these tests switch it on. A name with no `found`
// keeps today's card. The line is a quote of the table: drawn, never logged.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameCheckWords as nw } from "../src/content/recap-name-checks";
import { nameFoundWords as fw } from "../src/content/name-found-words";

const live = vi.hoisted(() => ({ found: true }));
vi.mock("../src/content/name-found-switch", () => ({
  get NAME_FOUND_LIVE() {
    return live.found;
  },
}));
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
  };
});

afterEach(() => {
  cleanup();
  live.found = true;
  vi.restoreAllMocks();
});

const draft = mockRecaps()[0]!;
const row = (name: string) => screen.getByRole("group", { name }) as HTMLElement;

async function open(api = mockApi("table")) {
  render(<RecapNameChecks api={api} refId={draft.ref} />);
  await screen.findByRole("heading", { name: nw.heading });
  return api;
}

describe("a name the session-names reader found", () => {
  it("shows how it was heard, DMbot's guess and the line that made it a name", async () => {
    await open();
    const card = within(row("Garrick"));
    expect(card.getByText("Garrick: a new name? Heard 5 times, sometimes as Garrik or gar rick.")).toBeTruthy();
    expect(card.getByText(fw.guess("A person"))).toBeTruthy();
    expect(card.getByText(fw.line("Mira", "This is Garrick, the ferryman."))).toBeTruthy();
  });

  it("Yes, spelled X answers with the guessed kind", async () => {
    const api = await open();
    const spy = vi.spyOn(api, "answerNameCheck");
    fireEvent.click(within(row("Garrick")).getByRole("button", { name: fw.yesSpelledFor("Garrick") }));
    expect(spy).toHaveBeenCalledWith(draft.ref, "c".repeat(32), { action: "confirm", kind: "npc" });
  });

  it("a name with no found row keeps today's card", async () => {
    await open();
    const card = within(row("Veshla"));
    expect(card.queryByText(fw.lineLabel)).toBeNull();
    expect(card.queryByRole("button", { name: fw.yesSpelledFor("Veshla") })).toBeNull();
  });

  it("with no line sent (its speaker stopped agreeing) nothing stands in its place", async () => {
    const api = mockApi("table");
    const real = api.nameChecks!.bind(api);
    api.nameChecks = async (...a) => {
      const got = await real(...a);
      return { ...got, names: got.names.map((n) => (n.found ? { ...n, found: { ...n.found, line: null } } : n)) };
    };
    await open(api);
    expect(within(row("Garrick")).queryByText(fw.lineLabel)).toBeNull();
  });

  it("shows nothing of it while the switch is off", async () => {
    live.found = false;
    await open();
    expect(screen.queryByText(/a new name\? Heard/)).toBeNull();
    expect(screen.queryByRole("button", { name: fw.yesSpelledFor("Garrick") })).toBeNull();
  });
});

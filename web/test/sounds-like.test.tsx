/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Sounds like X. The same, or new?" on a name to check (#5215; core's `card_match`): Discord's own words, a button "It's X" that joins the
// waiting name to the known one (the `same` answer), and nothing at all when core sends no match.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameCheckWords as nw } from "../src/content/recap-name-checks";

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

afterEach(() => cleanup());

const draft = mockRecaps()[0]!;

function withLike(like: { id: string; name: string } | null | undefined) {
  const api = mockApi("table");
  const real = api.nameChecks!.bind(api);
  api.nameChecks = async (...a) => {
    const got = await real(...a);
    return { ...got, names: got.names.map((n) => (n.id === "b".repeat(32) && like !== undefined ? { ...n, soundsLike: like } : n)) };
  };
  return api;
}

async function open(api: ReturnType<typeof mockApi>) {
  render(<RecapNameChecks api={api} refId={draft.ref} />);
  await screen.findByRole("heading", { name: nw.heading });
  return within(screen.getByRole("group", { name: "Veshla" }));
}

describe("a waiting name that sounds like a known one", () => {
  it("asks 'Sounds like X. The same, or new?' in Discord's words", async () => {
    const card = await open(withLike({ id: "k".repeat(32), name: "Veshla's Hall" }));
    expect(card.getByRole("group", { name: "Sounds like Veshla's Hall. The same, or new?" })).toBeTruthy();
    expect(card.getByText("Veshla's Hall", { selector: "strong" })).toBeTruthy();
  });

  it("'It's X' joins them with the same answer as 'It's the same as…'", async () => {
    const api = withLike({ id: "k".repeat(32), name: "Veshla's Hall" });
    const spy = vi.spyOn(api, "answerNameCheck");
    const card = await open(api);
    fireEvent.click(card.getByRole("button", { name: nw.itsThatFor("Veshla's Hall", "Veshla") }));
    expect(spy).toHaveBeenCalledWith(draft.ref, "b".repeat(32), { action: "same", known: "k".repeat(32) });
  });

  it("answering it as new still works as usual", async () => {
    const api = withLike({ id: "k".repeat(32), name: "Veshla's Hall" });
    const card = await open(api);
    expect(card.getByLabelText(nw.kind)).toBeTruthy();
    expect(card.getByRole("button", { name: nw.notANameFor("Veshla") })).toBeTruthy();
  });

  it("shows nothing when core sends no match, or an older API sends the key not at all", async () => {
    for (const like of [null, undefined]) {
      const card = await open(withLike(like));
      expect(card.queryByText(/Sounds like/)).toBeNull();
      cleanup();
    }
  });
});

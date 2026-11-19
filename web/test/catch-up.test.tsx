/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Catch up" on a name's row (#4793, #4824): who is behind on an entry, catching one up with ↩️ Undo, the pick list for several, and
// the words for each error. DM only; nothing is drawn when nobody is behind.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import CatchUp from "../src/account/CatchUp";
import { mockApi } from "../src/account/mock";
import { catchUpWords as w } from "../src/content/catch-up-words";

afterEach(() => cleanup());

const REF = "100.cmp1";

/** The card on an entry, opened from its button. */
async function open(entry: string, api = mockApi("table"), ref = REF) {
  render(<CatchUp api={api} refId={ref} entry={entry} />);
  fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
  return api;
}

describe("catch up on a name's row", () => {
  it("one member behind shows the line and the one button, with the count", async () => {
    await open("n25");
    expect(await screen.findByText(w.one("Ash", 2))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.oneLabel("Ash") })).toBeTruthy();
  });

  it("several members behind show who, and the pick list with each count and Back", async () => {
    await open("n24");
    expect(await screen.findByText("Wren and Ash are missing things on this card that the rest of the party knows.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.manyLabel }));
    expect(await screen.findByText(w.pickQuestion)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Wren (1 thing)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ash (3 things)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.back })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.back }));
    expect(await screen.findByText("Wren and Ash are missing things on this card that the rest of the party knows.")).toBeTruthy();
  });

  it("Done says what was told, and Undo says what was taken back", async () => {
    await open("n24");
    await screen.findByText("Wren and Ash are missing things on this card that the rest of the party knows."); // (loaded: the open button is the pick button now)
    fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
    fireEvent.click(await screen.findByRole("button", { name: "Wren (1 thing)" }));
    expect(await screen.findByText("✅ Wren now knows the 1 thing on this card the rest of the party knew. Nothing was sent to Wren's player.")).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText("↩️ Undone. Wren no longer knows those 1 thing.")).toBeTruthy();
  });

  it("the more line says how many were left out, and is gone when none", async () => {
    await open("n26");
    expect(await screen.findByText(w.moreMany(2))).toBeTruthy();
    expect(screen.getByText("2 more are missing things on this card too. Catch these up first and they will show.")).toBeTruthy();
  });

  it("the more line has the one-member sentence for one left out", async () => {
    const api = mockApi("table");
    api.catchUpChoices = async () => ({ choices: [{ id: "p1".repeat(16), count: 1 }], more: 1 });
    await open("n25", api);
    expect(await screen.findByText("1 more is missing things on this card too. Catch these up first and it will show.")).toBeTruthy();
  });

  it("no more line when none were left out", async () => {
    await open("n24");
    await screen.findByText("Wren and Ash are missing things on this card that the rest of the party knows.");
    expect(screen.queryByText(/more are missing things|more is missing things/)).toBeNull();
  });

  it("a member who is not in the party says so and changes nothing", async () => {
    await open("n25");
    fireEvent.click(await screen.findByRole("button", { name: w.oneLabel("Ash") }));
    expect(await screen.findByText(w.notInParty("Ash"))).toBeTruthy();
  });

  it("a member with nothing left says so in words", async () => {
    const api = mockApi("table");
    await open("n25", api);
    api.catchUp = async () => {
      throw new ApiError("nothing-left");
    };
    fireEvent.click(await screen.findByRole("button", { name: w.oneLabel("Ash") }));
    expect(await screen.findByText(w.nothingLeft("Ash"))).toBeTruthy();
  });

  it("an Undo that is too late says so, and names the card to use instead", async () => {
    const api = mockApi("table");
    await open("n24", api);
    await screen.findByText("Wren and Ash are missing things on this card that the rest of the party knows."); // (loaded: the open button is the pick button now)
    fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
    fireEvent.click(await screen.findByRole("button", { name: "Wren (1 thing)" }));
    await screen.findByText("✅ Wren now knows the 1 thing on this card the rest of the party knew. Nothing was sent to Wren's player.");
    api.partyUndo = async () => {
      throw new ApiError("undo-late");
    };
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.tooLate("Wren"))).toBeTruthy();
  });

  it("a second press while one waits sends nothing", async () => {
    const api = await open("n25");
    const button = await screen.findByRole("button", { name: w.oneLabel("Ash") });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("catchUp:"))).toHaveLength(1));
  });

  it("says so when nobody is behind, and the button stays to close it", async () => {
    render(<CatchUp api={mockApi("table")} refId={REF} entry="none" />);
    fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
    expect(await screen.findByText("Nothing to catch up on this card.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.oneLabel("Ash") })).toBeNull();
    const close = screen.getByRole("button", { name: w.manyLabel });
    expect(close.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(close);
    expect(screen.queryByText("Nothing to catch up on this card.")).toBeNull();
    expect(screen.getByRole("button", { name: w.manyLabel }).getAttribute("aria-expanded")).toBe("false");
  });

  it("a read that fails says so and is not taken for nobody behind", async () => {
    const api = mockApi("table");
    api.catchUpChoices = async () => {
      throw new ApiError("failed");
    };
    render(<CatchUp api={api} refId={REF} entry="n25" />);
    fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the catch-up.");
    expect(screen.queryByText("Nothing to catch up on this card.")).toBeNull();
  });

  it("a player's page draws nothing and makes no catch-up call", async () => {
    const api = mockApi("table");
    render(<CatchUp api={api} refId="100.cmp1.web-player" entry="n25" />);
    fireEvent.click(await screen.findByRole("button", { name: w.manyLabel }));
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the catch-up.");
    expect(screen.queryByRole("button", { name: w.oneLabel("Ash") })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("catchUp:"))).toBe(false);
  });

  it("cuts a long name in a label as core does, never the count", () => {
    expect(w.oneLabel("Mira")).toBe("📖 Catch up: Mira");
    expect(w.oneLabel("Mira Stormborn-of-the-Northern-Wastes")).toBe("📖 Catch up: Mira Stormbo…");
    expect(w.pickLabel("Mira", 1)).toBe("Mira (1 thing)");
    expect(w.pickLabel("Mira Stormborn-of-the-Northern-Wastes", 1)).toBe("Mira Stormborn… (1 thing)");
    expect(w.pickLabel("Mira Stormborn-of-the-Northern-Wastes", 6)).toBe("Mira Stormbor… (6 things)");
  });
});

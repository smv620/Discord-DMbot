/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "👥 Some know" on a waiting claim (#4680 part 1): the picker lists the party with nobody ticked; Save sends the ticked ids once
// and says who knows it; Cancel changes nothing; Undo takes it back by its batch; each failure says its own words.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type WaitingAnswer } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

// The 👥 button is behind SOME_KNOW_LIVE (off until deploy 37 carries migration 0535); these tests turn it on.
const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/some-know-switch", () => ({
  get SOME_KNOW_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

let n = 0;
/** The claims are the pretend list ("Mira Voss knows Far Hall" can be secret; "Roan is dead" is a condition); `sent` records each answer. */
async function open(ending = "") {
  const api = mockApi("table");
  const sent: WaitingAnswer[] = [];
  const real = api.answerWaiting!.bind(api);
  api.answerWaiting = async (ref, answer, onLook) => {
    sent.push(answer);
    return real(ref, answer, onLook);
  };
  render(<WebWaiting api={api} campaignRef={`100.some${(n += 1)}${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return { api, sent };
}
const panelOf = (statement: string) => screen.getByText(statement).closest(".panel") as HTMLElement;
const someFor = (statement: string) => within(panelOf(statement)).getByRole("button", { name: w.someButton });

describe("Some know on a waiting claim", () => {
  it("opens the party list with nobody ticked", async () => {
    await open();
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    expect(await screen.findByText(w.someLegend)).toBeTruthy();
    const boxes = (await screen.findAllByRole("checkbox")) as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([false, false]);
    expect(screen.getByText(w.someHelp)).toBeTruthy();
    expect((screen.getByRole("button", { name: w.someSave }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("save sends the ticked ids once and shows Known to", async () => {
    const { sent } = await open();
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    fireEvent.click((await screen.findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click((await screen.findByRole("checkbox", { name: "Ash" })) as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.someSave }));
    expect(await screen.findByText(w.someDoneMany("Wren, Ash"))).toBeTruthy();
    expect(sent.filter((a) => a.kind === "claim" && a.action === "some")).toEqual([
      { kind: "claim", action: "some", id: "c1", characters: ["p1", "p2"] },
    ]);
  });

  it("save is off until one is ticked, and cancel changes nothing", async () => {
    const { sent } = await open();
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    expect((screen.getByRole("button", { name: w.someSave }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    expect((screen.getByRole("button", { name: w.someSave }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.someCancel }));
    expect(screen.queryByText(w.someLegend)).toBeNull();
    expect(sent.some((a) => a.kind === "claim" && a.action === "some")).toBe(false);
  });

  it("undo sends the claim undo with the batch", async () => {
    const { sent } = await open();
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    fireEvent.click((await screen.findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.someSave }));
    await screen.findByText(w.someDoneOne("Wren"));
    await waitFor(() => expect((screen.getByRole("button", { name: w.someUndo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.someUndo }));
    expect(await screen.findByText(w.someUndone)).toBeTruthy();
    expect(sent).toContainEqual({ kind: "claim", action: "undo", id: "c1", batch: 101 });
  });

  it("gone and not_found show their words", async () => {
    const { api } = await open();
    api.answerWaiting = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    fireEvent.click((await screen.findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.someSave }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.someGone);
  });

  it("a not-found reply shows its own words too", async () => {
    const { api } = await open();
    api.answerWaiting = async () => {
      throw new ApiError("not-found");
    };
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    fireEvent.click((await screen.findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.someSave }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.someNotFound);
  });

  it("the button is not shown while the switch is off", async () => {
    live.on = false;
    await open();
    expect(within(panelOf("Mira Voss knows Far Hall")).queryByRole("button", { name: w.someButton })).toBeNull();
  });

  it("a condition claim has no Some know button", async () => {
    await open();
    expect(within(panelOf("Roan is dead")).queryByRole("button", { name: w.someButton })).toBeNull();
  });

  it("an empty party says so and cannot save", async () => {
    await open(".party-none");
    fireEvent.click(someFor("Mira Voss knows Far Hall"));
    expect(await screen.findByText(w.someEmpty)).toBeTruthy();
    expect((screen.getByRole("button", { name: w.someSave }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Some know on a waiting backstory connection (#4728)", () => {
  /** The panel of the backstory card with this text (its proposed line has the id `wb-…`). */
  const cardOf = (id: string) => document.getElementById(`wb-${id}`)!.closest(".panel") as HTMLElement;
  const someIn = (id: string) => within(cardOf(id)).queryByRole("button", { name: w.someButton });

  it("a ready backstory card opens the same picker with nobody ticked", async () => {
    await open();
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someButton }));
    const boxes = (await within(cardOf("b1")).findAllByRole("checkbox")) as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([false, false]);
    expect(within(cardOf("b1")).getByText(w.someBackstoryNote)).toBeTruthy();
  });

  it("save sends the ticked ids for the backstory card and says Known to, held back", async () => {
    const { sent } = await open();
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someButton }));
    fireEvent.click((await within(cardOf("b1")).findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someSave }));
    expect(await screen.findByText(w.someDoneBackstoryOne("Wren"))).toBeTruthy();
    expect(sent).toContainEqual({ kind: "backstory", action: "some", id: "b1", characters: ["p1"] });
  });

  it("a hand-over card, a secret card and a card not ready have no 👥 button", async () => {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    api.webWaiting = async (ref) => {
      const got = await real(ref);
      return { ...got, backstory: got.backstory.map((b) => (b.id === "b1" ? { ...b, handOver: "sold" as const, handOverName: "Mira Voss" } : b)) };
    };
    render(<WebWaiting api={api} campaignRef={`100.some${(n += 1)}`} />);
    await screen.findByRole("heading", { name: w.heading });
    expect(someIn("b1")).toBeNull(); // a hand-over
    expect(someIn("b2")).toBeNull(); // a secret name
    expect(someIn("b3")).toBeNull(); // not ready: a name in it is not confirmed yet
  });

  it("a refused pick shows its own words", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw new ApiError("bad-target");
    };
    render(<WebWaiting api={api} campaignRef={`100.some${(n += 1)}`} />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someButton }));
    fireEvent.click((await within(cardOf("b1")).findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someSave }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.someBackstoryRefused);
  });

  it("a backstory card has no undo after a some know", async () => {
    await open();
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someButton }));
    fireEvent.click((await within(cardOf("b1")).findByRole("checkbox", { name: "Wren" })) as HTMLElement);
    fireEvent.click(within(cardOf("b1")).getByRole("button", { name: w.someSave }));
    await screen.findByText(w.someDoneBackstoryOne("Wren"));
    expect(screen.queryByRole("button", { name: w.someUndo })).toBeNull();
  });

  it("the 👥 button is not on a backstory card while the switch is off", async () => {
    live.on = false;
    await open();
    expect(someIn("b1")).toBeNull();
  });
});

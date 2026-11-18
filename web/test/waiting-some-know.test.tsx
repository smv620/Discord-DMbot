/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "👥 Some know" on a waiting claim (#4680 part 1): the picker lists the party with nobody ticked; Save sends the ticked ids once
// and says who knows it; Cancel changes nothing; Undo takes it back by its batch; each failure says its own words.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type WaitingAnswer } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(cleanup);

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

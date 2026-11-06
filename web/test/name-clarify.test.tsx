/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The page for one name to check (#3604): the scene and where it was said at once, DMbot's best guesses, a search over the
// campaign's own names, each answer with its Undo, "Trivial" asking once, a player getting nothing, and the switch off
// hiding the page. Runs against the pretend API.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Clarify from "../src/account/Clarify";
import ClarifyIsland from "../src/account/ClarifyIsland";
import { clarifyWords as w } from "../src/content/name-clarify-words";

afterEach(cleanup);

const slow = { timeout: 9000 };
const ID = "a".repeat(32);
let count = 0;
const open = async (ending = "") => {
  const api = mockApi("table");
  render(<Clarify api={api} refId={`100.cl${(count += 1)}${ending}`} nameId={ID} />);
  await screen.findByRole("heading", { name: "Brannoch" }, slow);
  return api;
};
const calls = (api: ReturnType<typeof mockApi>, start: string) => api.calls.filter((c) => c.startsWith(start));

describe("the page of one name", () => {
  it("is hidden while its switch is off, and asks for nothing", () => {
    render(<ClarifyIsland />);
    expect(screen.getByText(w.notYet)).toBeTruthy();
  });

  it("shows the scene and where it was said without a press, and DMbot's best guesses", async () => {
    await open();
    expect(screen.getByText(/mends Mira's sword/)).toBeTruthy();
    expect(screen.getByText(/we rode to Brannoch's forge/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "a.k.a. Veshla" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "new NPC" })).toBeTruthy();
  });

  it("says so when DMbot has no scene and no guess yet", async () => {
    await open(".no-scene.no-answers");
    expect(screen.getByText(w.noScene)).toBeTruthy();
    expect(screen.getByText(w.noTop)).toBeTruthy();
  });

  it("gives a player nothing", async () => {
    const api = mockApi("table");
    render(<Clarify api={api} refId="100.cl.player" nameId={ID} />);
    expect(await screen.findByText(w.notDm, {}, slow)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Brannoch" })).toBeNull();
  });

  it("sends a best guess, says what happened, and Undo puts the name back", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: "new NPC" }));
    expect(await screen.findByText(w.done.confirm("Brannoch"), {}, slow)).toBeTruthy();
    expect(calls(api, "answerNameCheck")[0]).toContain('"action":"confirm"');
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone("Brannoch"), {}, slow)).toBeTruthy();
    expect(calls(api, "answerNameCheck")[1]).toContain('"action":"undo","batch":11');
    expect(await screen.findByRole("heading", { name: "Brannoch" }, slow)).toBeTruthy();
  }, 30000);

  it("searches only this campaign's names, asks once, and joins on Yes", async () => {
    const api = await open();
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "mira" } });
    fireEvent.click(await screen.findByRole("button", { name: /^Same person as Mira Voss/ }, slow));
    expect(calls(api, "allNames")[0]).toContain(":q=mira");
    expect(calls(api, "allNames")[0]).toMatch(/allNames:100\.cl\d+:/); // (this campaign's reference)
    expect(calls(api, "answerNameCheck")).toHaveLength(0); // asking changes nothing
    expect(screen.getByText(w.askSame("Brannoch", "Mira Voss"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.yes }));
    expect(await screen.findByText(w.done.same("Brannoch", "Mira Voss"), {}, slow)).toBeTruthy();
    expect(calls(api, "answerNameCheck")[0]).toContain('"action":"same"');
  }, 30000);

  it("asks once before dropping a trivial name, and sends nothing on No", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.trivial }));
    expect(screen.getByText(w.trivialAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.trivialNo }));
    expect(calls(api, "answerNameCheck")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: w.trivial }));
    fireEvent.click(screen.getByRole("button", { name: w.trivialYes }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(1));
    expect(calls(api, "answerNameCheck")[0]).toContain('"action":"not_a_name"');
    expect(await screen.findByText(w.done.not_a_name("Brannoch"), {}, slow)).toBeTruthy();
  }, 30000);
});

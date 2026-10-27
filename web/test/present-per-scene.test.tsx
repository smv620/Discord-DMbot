/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Who was in each scene" (#2577), against the shapes agreed on the issue: each scene's list, the DM's
// change saved as a whole list, the refusals in plain words (a recap that changed is read again), and
// nothing for a recap with no scenes or that can't be edited.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { useState } from "preact/hooks";
import { afterEach, describe, expect, it } from "vitest";

import type { Recap } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import PresentPerScene from "../src/account/PresentPerScene";
import { presentWords as w } from "../src/content/present-words";

afterEach(cleanup);

const DRAFT = "200000000000000001.cmp1.1791000000";
const POSTED = "200000000000000001.cmp1.1790000000";

function Page({ api, first }: { api: ReturnType<typeof mockApi>; first: Recap }) {
  const [recap, setRecap] = useState(first);
  return <PresentPerScene api={api} recap={recap} onRecap={setRecap} />;
}
async function open(ref = DRAFT) {
  const api = mockApi("table");
  const recap = await api.recap!(ref);
  render(<Page api={api} first={recap} />);
  return api;
}
const scene = (n: number): string => screen.getByText(`Scene ${n}`).closest("li")!.textContent ?? "";

describe("Who was in each scene", () => {
  it("lists who was in each scene", async () => {
    await open();
    expect(scene(1)).toContain("Mira, Tavian");
    expect(scene(2)).toContain(w.nobody);
  });

  it("saves the whole new list for a scene and says what it will do", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(2) }));
    fireEvent.click(screen.getByLabelText("Brannoch"));
    fireEvent.click(screen.getByLabelText("Tavian"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect(screen.getByText(w.saved(2))).toBeTruthy());
    expect(api.calls).toContain(`setBeatPresent:${DRAFT}:2:Tavian|Brannoch`);
    expect(scene(2)).toContain("Tavian, Brannoch");
    // The keyboard stays on that scene's line.
    await waitFor(() => expect(document.activeElement?.getAttribute("data-scene")).toBe("2"));
  });

  it("says the list is kept for this recap and that making it again deletes it, and offers no rewrite", async () => {
    const api = await open();
    expect(screen.getByText(w.lead).textContent).toContain("your saved lists are deleted");
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(2) }));
    fireEvent.click(screen.getByLabelText("Tavian"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    const said = await screen.findByText(w.saved(2));
    expect(said.textContent).toContain("kept for this recap");
    expect(screen.queryByRole("button", { name: /make it again/i })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("makeRecap"))).toBe(false);
  });

  it("marks the scene's list as the DM's after saving", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(2) }));
    fireEvent.click(screen.getByLabelText("Tavian"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.saved(2));
    const read = await api.recap!(DRAFT);
    expect(read.beats?.find((b) => b.n === 2)?.saved).toBe(true);
    expect(read.beats?.find((b) => b.n === 1)?.saved).toBeFalsy();
  });

  it("can be cancelled without saving", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(1) }));
    fireEvent.click(screen.getByLabelText("Brannoch"));
    fireEvent.click(screen.getByRole("button", { name: w.cancel }));
    expect(scene(1)).toContain("Mira, Tavian");
    expect(api.calls.some((c) => c.startsWith("setBeatPresent"))).toBe(false);
  });

  it("reads the recap again when it changed, and says so", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(3) }));
    fireEvent.click(screen.getByLabelText("Tavian"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["stale"]));
    expect(api.calls).toContain(`recap:${DRAFT}`);
    // The scenes now show what the newer text says, and the message is still there after the re-read.
    await waitFor(() => expect(scene(3)).toContain("Brannoch, Mira"));
    expect(screen.getByRole("alert").textContent).toBe(w.errors["stale"]);
  });

  it("refuses a name that isn't a character, keeps the editor open and says nothing was saved", async () => {
    const api = mockApi("table");
    const recap = await api.recap!(DRAFT);
    // DMbot listed someone who is no longer a character here: shown ticked, so the DM can untick it.
    recap.beats = [{ n: 1, present: ["Mira", "Gone Person"] }];
    render(<Page api={api} first={recap} />);
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(1) }));
    expect((screen.getByLabelText("Gone Person") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["bad-present"]));
    expect(screen.getByRole("button", { name: w.save })).toBeTruthy(); // still open, ticks kept
    fireEvent.click(screen.getByLabelText("Gone Person"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect(screen.getByText(w.saved(1))).toBeTruthy());
  });

  it("does not claim the scenes were updated when they could not be read again", async () => {
    const api = mockApi("table");
    const recap = await api.recap!(DRAFT);
    api.recap = async () => {
      throw new Error("offline");
    };
    render(<Page api={api} first={recap} />);
    fireEvent.click(screen.getByRole("button", { name: w.changeScene(3) }));
    fireEvent.click(screen.getByLabelText("Tavian"));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["reread"]));
    await waitFor(() => expect(document.activeElement?.getAttribute("data-scene")).toBe("3"));
  });

  it("offers no Change when the recap has no characters to pick from", async () => {
    const api = mockApi("table");
    const recap = { ...(await api.recap!(DRAFT)), characters: [] };
    render(<PresentPerScene api={api} recap={recap} onRecap={() => {}} />);
    expect(scene(1)).toContain("Mira");
    expect(screen.queryByRole("button", { name: /Change/ })).toBeNull();
  });

  it("changes nothing on a posted recap: the list is there, with no Change button", async () => {
    await open(POSTED);
    expect(scene(1)).toContain("Mira");
    expect(screen.queryByRole("button", { name: /Change/ })).toBeNull();
    expect(screen.getByText(w.locked)).toBeTruthy();
  });

  it("shows nothing for a recap with no scenes", async () => {
    const api = mockApi("table");
    const { beats: _scenes, ...recap } = await api.recap!(DRAFT);
    render(<PresentPerScene api={api} recap={recap} onRecap={() => {}} />);
    expect(screen.queryByRole("heading")).toBeNull();
  });
});

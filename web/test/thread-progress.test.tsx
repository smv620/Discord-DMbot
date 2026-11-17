/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The 🧵 Progress to check card on the Names page (#4616): the first thread's lines ticked and where they were heard; Keep these and
// None happened each one change for the bot; unticked lines are turned down; Undo takes a press back; each failure said plainly.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, NAMES_METHODS, type ThreadProgressThread } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import ThreadProgress from "../src/account/ThreadProgress";
import { threadProgressWords as w } from "../src/content/threads-words";

afterEach(cleanup);

const REF = "100.cmp1";
const A = "a".repeat(32);
const B = "b".repeat(32);
const C = "c".repeat(32);
const dated = new Date(1790000000 * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

function open(list?: ThreadProgressThread[]) {
  const api = mockApi("table");
  if (list) api.threadProgress = async () => structuredClone(list);
  render(<ThreadProgress api={api} refId={REF} />);
  return api;
}

describe("the 🧵 Progress to check card", () => {
  it("shows the first thread with its lines ticked and where it was heard", async () => {
    open();
    expect(await screen.findByText(`Find Garret: did these happen? DMbot heard them in a session on ${dated}.`)).toBeTruthy();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true, true]);
    expect(screen.getByText("New · In the way: the cave is trapped")).toBeTruthy();
  });

  it("Keep these sends the ticked and unticked ids and says what was kept", async () => {
    const api = open();
    await screen.findByText("New · In the way: the cave is trapped");
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(await screen.findByText(w.kept(3, "Find Garret"))).toBeTruthy();
    expect(api.calls).toContain(`changeThreadProgress:${REF}:keep:${A},${B},${C}`);
  });

  it("unticking a line sends it as off", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    fireEvent.click(screen.getAllByRole("checkbox")[1] as HTMLElement); // "Done: find Garret"
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    await screen.findByText(w.kept(2, "Find Garret"));
    expect(api.calls.some((c) => c === `changeThreadProgress:${REF}:keep:${A},${B},${C}`)).toBe(false);
    expect(api.calls.some((c) => c.startsWith(`changeThreadProgress:${REF}:keep:`) && c.endsWith(`${B}`))).toBe(true);
  });

  it("Keep with nothing ticked says it was left as it was", async () => {
    open();
    await screen.findByText("Done: find Garret");
    for (const box of screen.getAllByRole("checkbox")) fireEvent.click(box as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(await screen.findByText(w.leftAsIs("Find Garret"))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("None happened sends every shown line", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    fireEvent.click(screen.getByRole("button", { name: w.none }));
    expect(await screen.findByText(w.leftAsIs("Find Garret"))).toBeTruthy();
    expect(api.calls).toContain(`changeThreadProgress:${REF}:none:${A},${B},${C}`);
  });

  it("Undo after a keep sends the batch and says it is undone", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    await screen.findByText(w.kept(3, "Find Garret"));
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone("Find Garret"))).toBeTruthy();
    expect(api.calls).toContain(`changeThreadProgress:${REF}:undo:41`);
  });

  it("Undo after none sends the lines", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    fireEvent.click(screen.getByRole("button", { name: w.none }));
    await screen.findByText(w.leftAsIs("Find Garret"));
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone("Find Garret"))).toBeTruthy();
    expect(api.calls.some((c) => c === `changeThreadProgress:${REF}:undo:${A},${B},${C}`)).toBe(true);
  });

  it("Skip shows the next thread", async () => {
    open();
    await screen.findByText("Done: find Garret");
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    expect(await screen.findByText(`Free the Mill: did these happen? DMbot heard them in ${w.sessions(2)}.`)).toBeTruthy();
    expect(screen.getByRole("heading", { name: `${w.heading}${w.thread(2, 2)}` })).toBeTruthy();
  });

  it("more lines than shown says so", async () => {
    open([{ id: "t1", name: "Find Garret", kind: "quest", sessions: 1, sessionAt: null, more: 2, lines: [{ id: A, text: "Done: find Garret" }] }]);
    expect(await screen.findByText(w.more(2))).toBeTruthy();
    expect(screen.getByText(w.heard("Find Garret", w.oneSession(null)))).toBeTruthy();
  });

  it("an answered-meanwhile press says so and shows what is left", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    api.changeThreadProgress = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.gone);
  });

  it("a Keep that no longer fits says so", async () => {
    const api = open();
    await screen.findByText("Done: find Garret");
    api.changeThreadProgress = async () => {
      throw Object.assign(new ApiError("server"), { note: "changed" });
    };
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.changedKeep);
    await waitFor(() => expect((screen.getByRole("button", { name: w.keep }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("with no lines the card shows nothing", async () => {
    open([]);
    await waitFor(() => expect(screen.queryByRole("heading", { name: w.heading })).toBeNull());
  });

  it("the api-areas list includes threadProgress and changeThreadProgress", () => {
    expect(NAMES_METHODS).toContain("threadProgress");
    expect(NAMES_METHODS).toContain("changeThreadProgress");
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Say it in your own words" on a connection's panel (#5293, the website half of #2940; core #5298, behind CONNECTION_SAID_LIVE):
// the form and its limit, the wait, what DMbot understood as plain sentences, who knows, Use this with the ticked ones, one Undo,
// each way a read can fail in Discord's own words, a refusal before anything is read, and the panel's two entry points
// (Connect in words, and Correct it on a connection) that exist only while the switch is on and only for someone who may edit.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import ConnectionSaid from "../src/account/ConnectionSaid";
import ConnectionPanel from "../src/account/WebConnectionPanel";
import type { WebEdge } from "../src/account/api";
import { SAID_MAX, saidWords as w } from "../src/content/connection-said-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/connection-said-switch", () => ({
  get CONNECTION_SAID_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const slow = { timeout: 9000 };
const REF = "100.cmp1";
const calls = (api: ReturnType<typeof mockApi>, start: string) => api.calls.filter((c) => c.startsWith(start));
function show(over: { edit?: string } = {}) {
  const api = mockApi("table");
  const onClose = vi.fn();
  const onChanged = vi.fn();
  render(<ConnectionSaid api={api} refId={REF} a="a1" b="b1" {...(over.edit ? { edit: over.edit } : {})} onClose={onClose} onChanged={onChanged} />);
  return { api, onClose, onChanged };
}
const write = (text: string) => fireEvent.input(screen.getByLabelText(w.label), { target: { value: text } });
const read = async (text: string) => {
  write(text);
  fireEvent.click(screen.getByRole("button", { name: w.send }));
  await screen.findByText(w.readAs, {}, slow);
};

describe("the form", () => {
  it("asks for words first, and refuses more than the limit before sending anything", () => {
    const { api } = show();
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(screen.getByRole("alert").textContent).toBe(w.empty);
    write("x".repeat(SAID_MAX + 1));
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(screen.getByRole("alert").textContent).toBe(w.tooLong);
    expect(calls(api, "saidSend")).toEqual([]);
  });

  it("sends the two names and the words, and the connection being corrected", async () => {
    const { api } = show({ edit: "l2" });
    await read("Mara owes Tavin the favour.");
    expect(calls(api, "saidSend")).toEqual(["saidSend:100.cmp1:a1:b1:l2:27"]);
  });

  it("says what a refused ask means, in plain words", async () => {
    show();
    write("hourfull");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.refused["hour-full"]);
    expect(screen.getByLabelText(w.label)).toBeTruthy(); // (the form is still there, with its words)
  });
});

describe("what DMbot understood", () => {
  it("shows the wait, then each connection as a plain sentence and who knows", async () => {
    show();
    write("Mara owes Tavin.");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect(await screen.findByText(/DMbot is reading|Waiting for DMbot/, {}, slow)).toBeTruthy();
    await screen.findByText(w.readAs, {}, slow);
    expect(screen.getByText("Mara “owes” Tavin")).toBeTruthy();
    expect(screen.getByText("Tavin knows Mara")).toBeTruthy();
    expect((screen.getByRole("radio", { name: w.who.everyone }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(`${w.nothingYet} ${w.canUndo}`)).toBeTruthy();
  });

  it("starts on Only me when DMbot read it as a secret, and says a correction replaces the old one", async () => {
    show({ edit: "l1" });
    await read("A secret between them.");
    expect((screen.getByRole("radio", { name: w.who.dm }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(`${w.nothingYet} ${w.replaces}`)).toBeTruthy();
    expect(screen.getByRole("radio", { name: w.who.keep })).toBeTruthy(); // (a secret one being corrected may stay as it was)
  });

  it("says who it could not place, and keeps it with the DM", async () => {
    show();
    await read("A stranger knows.");
    expect(screen.getByText(new RegExp("I couldn't tell who “Zed” is"))).toBeTruthy();
  });

  it("goes back to the form with the words kept and Discord's sentence when it finds nothing", async () => {
    show();
    write("nothing here");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toContain(w.failed.nothing as string);
    expect((screen.getByLabelText(w.label) as HTMLTextAreaElement).value).toBe("nothing here");
  });

  it("says the same for words it could not read", async () => {
    show();
    write("unreadable words");
    fireEvent.click(screen.getByRole("button", { name: w.send }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toContain(w.failed.unreadable as string);
  });
});

describe("using it", () => {
  it("writes only the ticked ones, and one Undo takes it back", async () => {
    const { api, onChanged, onClose } = show();
    await read("Mara owes Tavin.");
    fireEvent.click(screen.getByRole("checkbox", { name: "Tavin knows Mara" }));
    fireEvent.click(screen.getByRole("button", { name: w.use }));
    expect(await screen.findByText(w.used, {}, slow)).toBeTruthy();
    expect(calls(api, "saidUse")).toEqual(["saidUse:100.cmp1:said-1:1:everyone:"]);
    expect(onChanged).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone, {}, slow)).toBeTruthy();
    expect(calls(api, "saidUndo")).toEqual(["saidUndo:100.cmp1:said-1"]);
    expect(onChanged).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: w.close }));
    expect(onClose).toHaveBeenCalled();
  }, 30_000);

  it("sends the named characters when only they know it", async () => {
    const { api } = show();
    await read("Only Tavin knows.");
    fireEvent.click(screen.getByRole("radio", { name: w.who.some("Tavin") }));
    fireEvent.click(screen.getByRole("button", { name: w.use }));
    await screen.findByText(w.used, {}, slow);
    expect(calls(api, "saidUse")[0]).toBe("saidUse:100.cmp1:said-1:1,2:some:n1");
  });

  it("writes nothing with nothing ticked, and nothing on Write it again or Cancel", async () => {
    const { api, onClose } = show();
    await read("Mara owes Tavin.");
    fireEvent.click(screen.getByRole("checkbox", { name: "Mara “owes” Tavin" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Tavin knows Mara" }));
    fireEvent.click(screen.getByRole("button", { name: w.use }));
    expect(calls(api, "saidUse")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.again }));
    expect((screen.getByLabelText(w.label) as HTMLTextAreaElement).value).toBe("Mara owes Tavin.");
    fireEvent.click(screen.getByRole("button", { name: w.close }));
    expect(onClose).toHaveBeenCalled();
    expect(calls(api, "saidUse")).toEqual([]);
  });

  it("sends Use this once for a double press, and a failed Undo stays on done, never back on Use this", async () => {
    const { api } = show();
    await read("undofail now");
    const use = screen.getByRole("button", { name: w.use });
    fireEvent.click(use);
    fireEvent.click(use);
    await screen.findByText(w.used, {}, slow);
    expect(calls(api, "saidUse")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.undoFailed);
    expect(screen.queryByRole("button", { name: w.use })).toBeNull();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy(); // (it can be pressed again)
  }, 30_000);

  it("sends As it was before when a hidden connection is corrected, and offers it for no other read", async () => {
    const { api } = show({ edit: "l1" });
    await read("A secret between them.");
    fireEvent.click(screen.getByRole("radio", { name: w.who.keep }));
    fireEvent.click(screen.getByRole("button", { name: w.use }));
    await screen.findByText(w.used, {}, slow);
    expect(calls(api, "saidUse")[0]).toBe("saidUse:100.cmp1:said-1:1,2:keep:");
    cleanup();
    show();
    await read("A secret between them.");
    expect(screen.queryByRole("radio", { name: w.who.keep })).toBeNull();
  }, 30_000);

  it("says when part of it had changed since and was left", async () => {
    show();
    await read("partly changes");
    fireEvent.click(screen.getByRole("button", { name: w.use }));
    await screen.findByText(w.used, {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undonePartly, {}, slow)).toBeTruthy();
  }, 30_000);
});

describe("the connection panel's two ways in", () => {
  const link = (id: string): WebEdge["links"][number] => ({ id, words: "are rivals", secret: false, direction: "both" });
  const EDGE: WebEdge = { a: "a1", b: "b1", label: "are rivals", more: 1, links: [{ ...link("l1"), pairWords: "are rivals" }] };
  const names: Record<string, string> = { a1: "Cerric", b1: "Wit" };
  const panel = (canEdit = true) =>
    render(<ConnectionPanel edge={EDGE} nameOf={(id) => names[id] ?? id} kindOf={() => "npc"} canEdit={canEdit} act={(async () => null) as never} reveal={async () => null} revealing={new Set()} api={mockApi("table")} refId={REF} onClose={() => {}} />);

  it("offers Connect in words, and Correct it inside an opened connection", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: w.connect }));
    expect(await screen.findByLabelText(w.label, {}, slow)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.close }));
    fireEvent.click(screen.getAllByRole("button", { name: /rival/ }).find((b) => b.getAttribute("aria-controls")?.startsWith("web-link-")) as HTMLElement);
    fireEvent.click(await screen.findByRole("button", { name: w.correct }));
    expect(await screen.findByLabelText(w.label, {}, slow)).toBeTruthy();
  });

  it("offers neither to someone who may not edit, nor while the switch is off", () => {
    panel(false);
    expect(screen.queryByRole("button", { name: w.connect })).toBeNull();
    cleanup();
    live.on = false;
    panel();
    expect(screen.queryByRole("button", { name: w.connect })).toBeNull();
    expect(within(document.body).queryByRole("button", { name: w.correct })).toBeNull();
  });
});

describe("nothing happens while it is off", () => {
  it("asks for nothing", () => {
    live.on = false;
    const api = mockApi("table");
    render(<ConnectionPanel edge={{ a: "a1", b: "b1", label: "x", more: 0, links: [] }} nameOf={(id) => id} kindOf={() => "npc"} canEdit act={(async () => null) as never} reveal={async () => null} revealing={new Set()} api={api} refId={REF} onClose={() => {}} />);
    expect(calls(api, "said")).toEqual([]);
  });
});


/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A route's 🛣️ Length (#4392, page half of #4986): the DM sees DMbot's read marked and can set, keep and undo it, as in
// Discord; a player sees the line only; nothing shows with ROUTE_LENGTH_LIVE off.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type RouteLength } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RouteLength_ from "../src/account/RouteLength";
import { routeLengthWords as w } from "../src/content/route-length-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/route-length-switch", () => ({
  get ROUTE_LENGTH_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
const noop = () => undefined;
// "l1" is a route the pretend campaign knows; its length starts as DMbot's read of "two days by cart".
function panel(length: RouteLength | undefined, route = true, canEdit = true, api = mockApi("table")) {
  render(<RouteLength_ api={api} refId={REF} on="connection" id="l1" length={length} route={route} canEdit={canEdit} onChanged={noop} />);
  return api;
}
const read = { words: "two days by cart", read: true };
const ownWords = { words: "two days by cart", read: false };

describe("a DM's route length", () => {
  it("shows DMbot's read marked, with the button and the keep; a kept one has no keep button", () => {
    panel(read);
    expect(screen.getByText(`${w.line("two days by cart")}${w.readNote}`)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepButton })).toBeTruthy();
    cleanup();
    panel(ownWords);
    expect(screen.queryByRole("button", { name: w.keepButton })).toBeNull();
  });

  it("a route with no length offers 🛣️ Length only", () => {
    panel(undefined);
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.queryByText(/🛣️ Length:/)).toBeNull();
    expect(screen.queryByRole("button", { name: w.keepButton })).toBeNull();
  });

  it("Save sends the words and says saved, with an Undo; an empty box says taken off", async () => {
    const api = panel(undefined);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "three days" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.saved("three days"))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
    expect(api.calls).toContain("changeRouteLength:100.cmp1:set");
    cleanup();
    panel(ownWords);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.takenOff)).toBeTruthy();
  });

  it("the same words again says nothing changed and offers no Undo", async () => {
    panel(ownWords);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.nothingChanged)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("✅ Length is right keeps it; a keep that is already changed says so", async () => {
    panel(read);
    fireEvent.click(screen.getByRole("button", { name: w.keepButton }));
    expect(await screen.findByText(w.kept("two days by cart"))).toBeTruthy();
    cleanup();
    // The page still shows DMbot's read, but the length was changed since (another screen): the keep is refused as changed.
    const api = mockApi("table");
    await api.changeRouteLength!(REF, { action: "set", on: "connection", id: "l1", words: "a week" });
    panel(read, true, true, api);
    fireEvent.click(screen.getByRole("button", { name: w.keepButton }));
    expect(await screen.findByText(w.changed)).toBeTruthy();
  });

  it("Undo takes the change back through the route's undo, and says so", async () => {
    const api = panel(undefined);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "three days" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("changeRouteLength:") && c.endsWith(":undo"))).toBe(true);
  });

  it("a route that is gone says so", async () => {
    const api = mockApi("table");
    api.changeRouteLength = async () => {
      throw new ApiError("gone");
    };
    panel(undefined, true, true, api);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
  });
});

describe("the mock's Story Graph routes", () => {
  it("the mock's graph carries DMbot's read as a route with its length, marked for the DM", async () => {
    const api = mockApi("table");
    // The lonely pretend web has the connection l1 with DMbot's read (mock.ts startWeb).
    const g = await api.webGraph!(`${REF}.web-lonely`, { center: "", hops: 1 });
    const link = g.edges.flatMap((e) => e.links).find((l) => l.id === "l1");
    expect(link?.route).toBe(true);
    expect(link?.length).toEqual({ words: "two days by cart", read: true });
  });
});

describe("a player's route length", () => {
  it("sees the line and no buttons once the length is kept, and never DMbot's unkept read", () => {
    panel(ownWords, false, false);
    expect(screen.getByText(w.line("two days by cart"), { exact: false })).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(w.readNote, { exact: false })).toBeNull();
    cleanup();
    panel(read, false, false);
    expect(screen.queryByText(w.line("two days by cart"), { exact: false })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("nothing is drawn with the switch off", () => {
    live.on = false;
    panel(read);
    expect(screen.queryByText(/🛣️/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("the page is read again after a change (#5035)", () => {
  it("a change calls onChanged once, and Undo calls it once more", async () => {
    let reads = 0;
    render(<RouteLength_ api={mockApi("table")} refId={REF} on="connection" id="l1" length={undefined} route canEdit onChanged={() => (reads += 1)} />);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "three days" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.saved("three days"));
    expect(reads).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    await screen.findByText(w.undone);
    expect(reads).toBe(2);
  });

  it("a new length from the page drops the local copy, so the panel shows what was read", async () => {
    const api = mockApi("table");
    const { rerender, container } = render(<RouteLength_ api={api} refId={REF} on="connection" id="l1" length={undefined} route canEdit onChanged={noop} />);
    // A change first, so the panel holds its own copy of it.
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "a day" } });
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    await screen.findByText(w.saved("a day"));
    expect(container.textContent).toContain(w.line("a day"));
    // The page reads the length again, and that new length is what shows.
    rerender(<RouteLength_ api={api} refId={REF} on="connection" id="l1" length={{ words: "a week", read: false }} route canEdit onChanged={noop} />);
    expect(container.textContent).toContain(w.line("a week"));
    expect(container.textContent).not.toContain(w.line("a day"));
  });

  it("a change that fails still reads the page again", async () => {
    let reads = 0;
    const api = mockApi("table");
    api.changeRouteLength = async () => {
      throw new ApiError("server");
    };
    render(<RouteLength_ api={api} refId={REF} on="connection" id="l1" length={undefined} route canEdit onChanged={() => (reads += 1)} />);
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: w.save }));
    expect(await screen.findByText(w.couldNot)).toBeTruthy();
    expect(reads).toBe(1);
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Line for the players" on a name of the Names page (#3568), against the pretend API. Shown only while PLAYERS_LINE_LIVE is
// true (false today), so these tests switch it on. DM only: a manager who can't change names never sees the box or a value.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as n } from "../src/content/names-words";
import { PLAYERS_LINE_MAX, playersLineWords as w } from "../src/content/players-line-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/players-line-switch", () => ({
  get PLAYERS_LINE_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "/account/names/100.cmp1";
// The link from Discord's "Say it" opens the editor of one name: the way these tests get to a name's editor.
const open = async (path = DM, api = mockApi("table")) => {
  render(<Names api={api} path={path} hash="#said=Gorrak" />);
  await screen.findByRole("list", { name: n.listLabel });
  return api;
};
// The Names page reads its list again right after the link opens the editor, which draws the editor anew: wait for it to settle.
const box = async () => {
  await screen.findByLabelText(w.label);
  await new Promise((r) => setTimeout(r, 300));
  return screen.getByLabelText(w.label) as HTMLInputElement;
};
/** Types a line and presses Save once the button has woken up (the page draws a moment after the typing). */
const write = async (line: string) => {
  fireEvent.input(await box(), { target: { value: line } });
  const save = screen.getByRole("button", { name: w.save }) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
};
const changes = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.includes('"action":"players-line"'));

describe("the line for the players", () => {
  it("has the exact label and saves a line, saying exactly what was saved", async () => {
    const api = await open();
    await write("  Gorrak   guards the gate ");
    await screen.findByText(w.saved("Gorrak guards the gate"));
    expect(changes(api)).toHaveLength(1);
    expect(changes(api)[0]).toContain('"line":"Gorrak guards the gate"');
  });

  it("changes it and then clears it with a blank, saying so", async () => {
    await open();
    await write("Gorrak guards the gate");
    await screen.findByText(w.saved("Gorrak guards the gate"));
    await write("Gorrak is the gate guard");
    await screen.findByText(w.saved("Gorrak is the gate guard"));
    await write("   ");
    await screen.findByText(w.removed);
  });

  it("refuses a line over the limit without sending it", async () => {
    const api = await open();
    await write("x".repeat(PLAYERS_LINE_MAX + 1));
    await screen.findByText(w.tooLong);
    expect(changes(api)).toHaveLength(0);
    await write("y".repeat(PLAYERS_LINE_MAX));
    await waitFor(() => expect(changes(api)).toHaveLength(1));
  });

  it("says plainly when it did not save", async () => {
    await open();
    await write("Gorrak guards the gate [fail]");
    await waitFor(() => expect(screen.getAllByRole("status").some((el) => el.textContent === n.errorFallback)).toBe(true));
  });

  it("is not there for a manager who can't change names, nor while the switch is off", async () => {
    await open("/account/names/100.cmp1.names-manager");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByLabelText(w.label)).toBeNull();
    cleanup();
    live.on = false;
    await open();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByLabelText(w.label)).toBeNull();
  });

  it("is read back by the page after a save: the list holds the new line, so a reopened editor shows it", async () => {
    const api = await open();
    await write("Gorrak guards the gate");
    await screen.findAllByText(w.saved("Gorrak guards the gate"));
    const again = await api.allNames!("100.cmp1", { q: "Gorrak", kind: "", sort: "name", page: 1 });
    expect(again.names[0]?.playersText).toBe("Gorrak guards the gate");
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("allNames")).length).toBeGreaterThan(2));
  });

  it("says the box is separate from the DM's own note and counts the letters", async () => {
    await open();
    await box();
    expect(screen.getByText(/Your own note stays private\./)).toBeTruthy();
    await write("Gorrak guards");
    expect(screen.getByText(new RegExp(`${"Gorrak guards".length} of ${PLAYERS_LINE_MAX}`))).toBeTruthy();
  });

  it("keeps the manager's list free of the value", async () => {
    const api = mockApi("table");
    await api.changeName!("100.cmp1", "n1", { action: "players-line", line: "Told to players" });
    const manager = await api.allNames!("100.cmp1.names-manager", { q: "", kind: "", sort: "name", page: 1 });
    expect(JSON.stringify(manager)).not.toContain("Told to players");
    expect(manager.names.every((row) => row.playersText === undefined)).toBe(true);
  });
});

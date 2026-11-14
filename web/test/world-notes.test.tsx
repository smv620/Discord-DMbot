/** @jsxImportSource preact */
// @vitest-environment happy-dom
// World notes on the Names page (#4285): DMbot's guesses for the DM to keep or turn down, in Discord's words, one Undo for each press,
// a line checked meanwhile says so, and nothing at all while the switch is off or for a player.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { worldNotesWords as w } from "../src/content/world-notes-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/world-notes-switch", () => ({
  get WORLD_NOTES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "/account/names/100.cmp1";
const SALT = "Saltmarsh: Swamp";

async function open(path = DM) {
  const api = mockApi("table");
  render(<Names api={api} path={path} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const calls = (api: ReturnType<typeof mockApi>, what: string) => api.calls.filter((c) => c.startsWith(`changeWorldNotes:`) && c.includes(what));

describe("the list", () => {
  it("shows each kind's lines in Discord's words, with where each came from", async () => {
    await open();
    expect(await screen.findByText(SALT)).toBeTruthy();
    expect(screen.getByText("Saltmarsh: Mild and wet")).toBeTruthy();
    expect(screen.getByText("Greywatch: Ruled by The Greywatch Guard")).toBeTruthy();
    expect(screen.getByText("Brother Venn: Giant")).toBeTruthy();
    expect(screen.getAllByText(/Land · from a session/).length).toBeGreaterThan(0);
    expect(screen.getByText("Language · from your lore")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepAllPlaces(3) })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepAllLanguages(1) })).toBeTruthy();
  });
  it("is not there while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<Names api={api} path={DM} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("worldNotes:"))).toBe(false);
  });
});

describe("the presses", () => {
  it("✅ Keep sends one line's id, says what was kept, and the line leaves the list", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    expect(await screen.findByText(w.kept(SALT))).toBeTruthy();
    expect(calls(api, '"action":"keep"')).toHaveLength(1);
    await waitFor(() => expect(screen.queryByText(SALT)).toBeNull());
  });
  it("Keep all sends the ids the DM was shown, as one press", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepAllPlaces(3) }));
    expect(await screen.findByText(w.keptMany(3))).toBeTruthy();
    const sent = calls(api, '"action":"keep"');
    expect(sent).toHaveLength(1);
    expect((sent[0]!.match(/place:/g) ?? []).length).toBe(3);
  });
  it("🚫 on a place says it won't be guessed again; on a language, that it can come back", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel(SALT) }));
    expect(await screen.findByText(new RegExp(`${w.placeNever}$`))).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel("Brother Venn: Giant") }));
    expect(await screen.findByText(new RegExp(`${w.languageAgain.replace(/[.]/g, "\\.")}$`))).toBeTruthy();
  });
  it("↩ Undo puts the line back", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(await screen.findByText(SALT)).toBeTruthy();
    expect(calls(api, '"action":"undo"')).toHaveLength(1);
  });
  it("a line checked meanwhile says so in plain words and the list is read again", async () => {
    const api = await open();
    const press = fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    expect(press).toBeDefined();
    await screen.findByText(w.kept(SALT));
    api.changeWorldNotes = async () => {
      throw new ApiError("gone");
    };
    const before = api.calls.filter((c) => c.startsWith("worldNotes:")).length;
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel("Greywatch: Ruled by The Greywatch Guard") }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("worldNotes:")).length).toBeGreaterThan(before));
  });
});

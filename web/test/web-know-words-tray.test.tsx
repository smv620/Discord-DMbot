/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Who knows each unconnected name, in the overview tray (#2999 slice 4, #3192): the same five states and rules as
// the graph. Only the DM's own full view carries a state; without one the older words stand.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

// The campaign whose tray holds names with no connection yet (as test/web-overview.test.tsx reads it).
const REF = "100.cmp1.web-lonely";
async function open(api = mockApi("table")) {
  render(<Web api={api} path={`/account/web/${REF}`} />);
  await screen.findByText(/names?, \d+ lines?/);
  await waitFor(() => expect(document.getElementById("web-tray-heading")).toBeTruthy());
  return api;
}
/** The tray section, found by its heading. */
const tray = (): HTMLElement => document.getElementById("web-tray-heading")?.closest("section") as HTMLElement;

describe("who knows each unconnected name, in the tray", () => {
  it("the tray shows the states its names have", async () => {
    const api = await open();
    // This campaign's tray holds four unconnected names, so four states show here; the graph test covers all five.
    const overview = await api.webOverview!(REF, { view: "full" });
    const states = overview.tray.map((n) => n.knowWord).filter((k): k is NonNullable<typeof k> => k !== undefined);
    expect(states.length).toBeGreaterThan(0);
    // Each name is tied to its own state: its button carries the words of its own state.
    for (const n of overview.tray.filter((x) => x.knowWord)) {
      const button = Array.from(tray().querySelectorAll("button")).find((b) => b.textContent?.includes(n.name));
      expect(button?.textContent ?? "", n.name).toContain(w.knowWords[n.knowWord!]);
    }
  });

  it("the tray marks 🤫 for secret only", async () => {
    const api = await open();
    const overview = await api.webOverview!(REF, { view: "full" });
    const named = overview.tray.filter((n) => n.knowWord);
    expect(named.length).toBeGreaterThan(0);
    for (const n of named) {
      const button = Array.from(tray().querySelectorAll("button")).find((b) => b.textContent?.includes(n.name));
      expect(button, n.name).toBeTruthy();
      expect(button?.textContent?.includes(w.secretMark) ?? false, n.name).toBe(n.knowWord === "secret");
    }
  });

  it("a tray read that is not the DM's full view never shows a state", async () => {
    const api = mockApi("table");
    const full = api.webOverview!.bind(api);
    api.webOverview = async (ref, q) => ({ ...(await full(ref, q)), canEdit: false });
    await open(api);
    const words = tray().textContent ?? "";
    for (const word of Object.values(w.knowWords)) expect(words).not.toContain(word);
    expect(words).not.toContain(w.knowHint);
  });

  it("with no knowWord the tray still lists its names and shows no state", async () => {
    const api = mockApi("table");
    const full = api.webOverview!.bind(api);
    api.webOverview = async (ref, q) => {
      const o = await full(ref, q);
      return { ...o, tray: o.tray.map(({ knowWord: _k, ...n }) => n) };
    };
    await open(api);
    expect(tray().textContent).not.toContain(w.knowHint);
    for (const word of Object.values(w.knowWords)) expect(tray().textContent).not.toContain(word);
    expect(tray().querySelectorAll("button").length).toBeGreaterThan(0);
  });

  it("the hint shows when a tray name has a state", async () => {
    await open();
    expect(tray().textContent).toContain(w.knowHint);
  });
});

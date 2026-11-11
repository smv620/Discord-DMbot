/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph says how an item changed hands (#4122, the web half of #4115): the API's own words, in the opened row of that item,
// with Discord's heading, behind HAND_OVER_LIVE; nothing for an item with no history, and nothing at all with the switch off.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/hand-over-switch", () => ({
  get HAND_OVER_LIVE() {
    return live.on;
  },
}));

import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(() => {
  cleanup();
  live.on = true;
});

const LINE = "Claude sold it to Belleros · Belleros has it now";

async function openFirst(): Promise<void> {
  const api = mockApi("table");
  const real = api.webGraph!.bind(api);
  api.webGraph = async (ref, query) => {
    const got = await real(ref, query);
    return { ...got, nodes: got.nodes.map((n) => ({ ...n, kind: "item", history: LINE })) };
  };
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findAllByRole("button", { expanded: false });
  fireEvent.click(document.querySelector('button[aria-controls^="web-name-"]') as HTMLElement);
}

describe("how an item changed hands", () => {
  it("is said under the item, with Discord's heading and the API's words", async () => {
    await openFirst();
    expect(await screen.findByText(w.historyHead)).toBeTruthy();
    expect(screen.getByText(LINE, { exact: false })).toBeTruthy();
  });

  it("is not said when the switch is off", async () => {
    live.on = false;
    await openFirst();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(w.historyHead)).toBeNull();
    expect(document.body.textContent).not.toContain(LINE);
  });
});

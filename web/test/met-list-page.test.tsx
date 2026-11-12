/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "👣 Met but not named" on the Names page (#4084) shows only for a DM and only while its switch is on.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { metWords as w } from "../src/content/met-list-words";
import { namesPage } from "../src/content/names-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/met-list-switch", () => ({
  get MET_LIST_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

describe("the Names page", () => {
  it("shows the list to a DM when its switch is on", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    expect(await screen.findByRole("heading", { name: new RegExp(w.heading) })).toBeTruthy();
  });

  it("does not ask for it while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<Names api={api} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    expect(api.calls.some((c) => c.startsWith("metList:"))).toBe(false);
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Names to check" is part of the DM's recap page (#2114 part 3). Kept apart from the other tests of
// the names because importing the whole recap page changes how happy-dom delivers a select's change.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { nameCheckWords as w } from "../src/content/recap-name-checks";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return live.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

it("is on the recap page under the fix box when it is switched on, and absent when off", async () => {
  const draft = mockRecaps()[0]!;
  render(<Recaps api={mockApi("table")} path={`/account/recaps/${draft.ref}`} />);
  await screen.findByRole("heading", { level: 2 });
  expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  cleanup();
  live.on = true;
  render(<Recaps api={mockApi("table")} path={`/account/recaps/${draft.ref}`} />);
  expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
});

it("is held with its What's new entry, and goes live with it", async () => {
  const { RECAP_NAME_CHECKS_LIVE: real } = await vi.importActual<typeof import("../src/content/recap-words")>("../src/content/recap-words");
  const { held } = await import("../src/content/whats-new");
  expect(held.map((h) => h.title).includes("Check a recap's new names online")).toBe(!real);
});

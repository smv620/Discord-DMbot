/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The 🧵 Progress to check card on the Names page (#4616) shows nothing while THREADS_LIVE is off. (The card's own behaviour is in
// thread-progress.test.tsx; the switched-on path through the Names page is not covered here.)
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Names from "../src/account/Names";
import { mockApi } from "../src/account/mock";
import { threadProgressWords as w } from "../src/content/threads-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/threads-switch", () => ({
  get THREADS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
});

describe("the Progress to check card on the Names page", () => {
  it("nothing shows while THREADS_LIVE is off", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: /./ }).catch(() => undefined);
    await waitFor(() => expect(screen.queryByRole("heading", { name: w.heading })).toBeNull());
  });

});

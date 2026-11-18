/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The table's guesses on the quest sheet (#4665): the lines as the API gives them without the markdown asterisks, nothing at all
// when there are none or a read is refused, and no buttons. The panel is mounted by the quest sheet only while
// THREAD_GUESSES_LIVE is on.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import QuestSheet from "../src/account/QuestSheet";
import ThreadGuesses from "../src/account/ThreadGuesses";
import { threadGuessWords as w } from "../src/content/thread-guesses-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/thread-guesses-switch", () => ({
  get THREAD_GUESSES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";

describe("The table's guesses on the quest sheet", () => {
  it("shows the lines without the markdown asterisks, one per line", async () => {
    render(<ThreadGuesses api={mockApi("table")} refId={REF} thread="t1" />);
    expect(await screen.findByText("💭 Guesses:")).toBeTruthy();
    expect(screen.getByText("• Cerric is sure Mira Vell poisoned the Old Well")).toBeTruthy();
    expect(screen.getByText("• The whole party thinks the miller lied")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("**");
  });

  it("shows nothing when there are no lines", async () => {
    const api = mockApi("table");
    render(<ThreadGuesses api={api} refId={REF} thread="t9" />);
    await waitFor(() => expect(api.calls).toContain("threadGuesses:100.cmp1:t9"));
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("a refused read shows nothing at all", async () => {
    const api = mockApi("table");
    render(<ThreadGuesses api={api} refId="100.cmp1.player" thread="t1" />);
    await waitFor(() => expect(api.calls).toContain("threadGuesses:100.cmp1.player:t1"));
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(screen.queryByText(/Guesses/)).toBeNull();
  });

  it("has no buttons and never says right or wrong", async () => {
    render(<ThreadGuesses api={mockApi("table")} refId={REF} thread="t2" />);
    await screen.findByText(/Oswin doubts/);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(document.body.textContent ?? "").not.toMatch(/\b(right|wrong|warm|cold|solved)\b/i);
  });

  it("is not shown while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    api.threadCard = async () => {
      throw new ApiError("not-found");
    };
    render(<QuestSheet api={api} refId={REF} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={() => {}} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.calls.some((c) => c.startsWith("threadGuesses:"))).toBe(false);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });
});

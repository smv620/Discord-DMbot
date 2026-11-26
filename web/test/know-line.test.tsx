/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "who knows it" line under a "Some know" pick (#5332, core #5338): as the DM ticks characters the page shows the line the pick
// will give, in Discord's words; nothing is asked while the switch is off or nobody is ticked; a failed look leaves the picker as it was.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

const live = vi.hoisted(() => ({ line: true }));
vi.mock("../src/content/some-know-switch", () => ({ SOME_KNOW_LIVE: true }));
vi.mock("../src/content/know-line-switch", () => ({
  get KNOW_LINE_LIVE() {
    return live.line;
  },
}));

afterEach(() => {
  cleanup();
  live.line = true;
});

let n = 0;
async function openPicker() {
  const api = mockApi("table");
  render(<WebWaiting api={api} campaignRef={`100.line${(n += 1)}`} />);
  await screen.findByRole("heading", { name: w.heading });
  const panel = screen.getByText("Mira Voss knows Far Hall").closest(".panel") as HTMLElement;
  fireEvent.click(Array.from(panel.querySelectorAll("button")).find((b) => b.textContent === w.someButton)!);
  await screen.findByText(w.someLegend);
  return api;
}

describe("the line under a Some know pick", () => {
  it("shows the line once someone is ticked, and changes it as the ticks change", async () => {
    const api = await openPicker();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    expect(await screen.findByText("👥 Known to: 1 of the party")).toBeTruthy();
    expect(api.calls).toContain("knowLine:100.line" + n + ":p1:false");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Ash" }));
    expect(await screen.findByText("👥 Known to: 2 of the party")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Wren" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Ash" }));
    await waitFor(() => expect(screen.queryByText(/Known to:/)).toBeNull());
  });

  it("asks nothing while the switch is off", async () => {
    live.line = false;
    const api = await openPicker();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    expect(screen.queryByText(/Known to:/)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("knowLine"))).toBe(false);
  });

  it("a look that fails leaves the picker as it was", async () => {
    const api = await openPicker();
    const asked = vi.fn(async () => {
      throw new Error("down");
    });
    api.knowLine = asked;
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    await waitFor(() => expect(asked).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByRole("button", { name: w.someSave }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText(/Known to:/)).toBeNull();
  });

  it("drops a late answer to an older tick", async () => {
    const api = await openPicker();
    const waiting: ((v: { line: string }) => void)[] = [];
    api.knowLine = vi.fn(() => new Promise<{ line: string }>((resolve) => waiting.push(resolve)));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Ash" }));
    await waitFor(() => expect(waiting).toHaveLength(2));
    waiting[1]!({ line: "✅ The whole party knows" });
    expect(await screen.findByText("✅ The whole party knows")).toBeTruthy();
    waiting[0]!({ line: "👥 Known to: Wren" });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText("👥 Known to: Wren")).toBeNull();
    expect(screen.getByText("✅ The whole party knows")).toBeTruthy();
  });

  it("Cancel takes the line away, and reopening the picker starts without one", async () => {
    await openPicker();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Wren" }));
    expect(await screen.findByText("👥 Known to: 1 of the party")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.someCancel }));
    expect(screen.queryByText(/Known to:/)).toBeNull();
    const panel = screen.getByText("Mira Voss knows Far Hall").closest(".panel") as HTMLElement;
    fireEvent.click(Array.from(panel.querySelectorAll("button")).find((b) => b.textContent === w.someButton)!);
    await screen.findByText(w.someLegend);
    expect(screen.queryByText(/Known to:/)).toBeNull();
  });
});

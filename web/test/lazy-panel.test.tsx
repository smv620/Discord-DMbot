/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A panel fetched on demand (#2690): it says it is loading, says when it is slow, shows the panel once the
// code arrives, and when the fetch fails says so and offers to try again.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LazyPanel from "../src/account/LazyPanel";
import { lazyWords as w } from "../src/content/lazy-panel-words";

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const Hello = ({ name }: { name: string }) => <p>Hello {name}</p>;

describe("LazyPanel", () => {
  it("says it is loading, then shows the panel", async () => {
    let arrive: (m: { default: typeof Hello }) => void = () => {};
    const load = () => new Promise<{ default: typeof Hello }>((r) => (arrive = r));
    render(<LazyPanel load={load} what="Names to check" props={{ name: "Mira" }} />);
    expect(screen.getByRole("status").textContent).toBe(w.loading("Names to check"));
    await act(async () => arrive({ default: Hello }));
    expect(screen.getByText("Hello Mira")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says plainly when it is slow", async () => {
    const load = () => new Promise<{ default: typeof Hello }>(() => {});
    render(<LazyPanel load={load} what="the player" props={{ name: "x" }} />);
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByRole("status").textContent).toBe(w.slow("the player"));
  });

  it("does not set state after it is gone, and fetches once per attempt", async () => {
    let arrive: (m: { default: typeof Hello }) => void = () => {};
    let calls = 0;
    const load = () => new Promise<{ default: typeof Hello }>((r) => ((calls += 1), (arrive = r)));
    const { rerender, unmount } = render(<LazyPanel load={load} what="the names to check" props={{ name: "a" }} />);
    rerender(<LazyPanel load={load} what="the names to check" props={{ name: "b" }} />);
    expect(calls).toBe(1);
    unmount();
    await act(async () => arrive({ default: Hello })); // (no warning, no update of a gone component)
    expect(calls).toBe(1);
  });

  it("passes new props through when the page draws again", async () => {
    const load = () => Promise.resolve({ default: Hello });
    const { rerender } = render(<LazyPanel load={load} what="the names to check" props={{ name: "Mira" }} />);
    await waitFor(() => expect(screen.getByText("Hello Mira")).toBeTruthy());
    rerender(<LazyPanel load={load} what="the names to check" props={{ name: "Tavian" }} />);
    expect(screen.getByText("Hello Tavian")).toBeTruthy();
  });

  it("offers Try again when it is stuck for a long time", async () => {
    const load = () => new Promise<{ default: typeof Hello }>(() => {});
    render(<LazyPanel load={load} what="the audio player" props={{ name: "x" }} />);
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    expect(screen.getByRole("status").textContent).toBe(w.stuck("the audio player"));
    expect(screen.getByRole("button", { name: w.retry })).toBeTruthy();
  });

  it("every label in the page reads right inside each sentence", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const page = readFileSync(join(process.cwd(), "src/account/Recaps.tsx"), "utf8");
    const labels = [...page.matchAll(/what="([^"]+)"/g)].map((m) => m[1] as string);
    expect(labels.length).toBeGreaterThan(5);
    for (const what of labels) {
      expect(what, what).toMatch(/^[a-z]/); // lower case, so "Loading … " and "Couldn't load …" read right
      expect(what, what).not.toMatch(/[.…!?]$/);
    }
  });

  it("says what to do when the code could not be fetched, and tries again", async () => {
    let tries = 0;
    const load = () => (++tries === 1 ? Promise.reject(new Error("offline")) : Promise.resolve({ default: Hello }));
    render(<LazyPanel load={load} what="Names to check" props={{ name: "Mira" }} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.failed("Names to check")));
    fireEvent.click(screen.getByRole("button", { name: w.retry }));
    await waitFor(() => expect(screen.getByText("Hello Mira")).toBeTruthy());
    expect(tries).toBe(2);
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The page's own clock for waits the API sends no progress for (#2565): "Running for" counts up and
// "last moved" resets only when what the page can see changes. Counts and stages only, never words.
import { act, cleanup, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useObservedWait } from "../src/account/useObservedWait";
import { workWords } from "../src/content/work-progress";

function Probe({ active, sig }: { active: boolean; sig: string }) {
  const w = useObservedWait(active, sig);
  return <p>{w.startedAt === null ? "idle" : `${(w.movedAt ?? 0) - w.startedAt}`}</p>;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T12:00:00Z"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useObservedWait", () => {
  it("is idle until a wait is active, and forgets it when it ends", () => {
    const { rerender } = render(<Probe active={false} sig="a" />);
    expect(screen.getByText("idle")).toBeTruthy();
    rerender(<Probe active sig="a" />);
    expect(screen.getByText("0")).toBeTruthy();
    rerender(<Probe active={false} sig="a" />);
    expect(screen.getByText("idle")).toBeTruthy();
  });

  it("moves 'last moved' only when what the page sees changes", () => {
    const { rerender } = render(<Probe active sig="a" />);
    act(() => void vi.advanceTimersByTime(10_000));
    rerender(<Probe active sig="a" />);
    expect(screen.getByText("0")).toBeTruthy(); // nothing changed, so it still reads the start
    act(() => void vi.advanceTimersByTime(5_000));
    rerender(<Probe active sig="b" />);
    expect(screen.getByText("15")).toBeTruthy();
  });

  it("has words for a stall that tell the person what to do", () => {
    expect(workWords.stalledDo).toBeTruthy();
  });
});

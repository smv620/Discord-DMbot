/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The shared wait component (#2565): stage, how far along, elapsed time that ticks, the last change, a stall
// and a failure in plain words. Counts and stages only.
import { act, cleanup, render, screen } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkProgress from "../src/account/WorkProgress";
import { stageWords, workWords as w } from "../src/content/work-progress";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-23T12:00:00Z"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const T0 = new Date("2026-10-23T12:00:00Z").getTime() / 1000;

describe("duration words", () => {
  it.each([
    [0, "0 sec"],
    [45, "45 sec"],
    [60, "1 min"],
    [125, "2 min"],
    [700, "11 min"],
    [3780, "1 h 3 min"],
    [7200, "2 h"],
    [-5, "0 sec"],
  ])("%s seconds reads %s", (seconds, expected) => {
    expect(w.duration(seconds)).toBe(expected);
  });
});

describe("the stage", () => {
  it("is looked up in the page's own words, and an unknown code falls back to plain words", () => {
    const stages = { reading: "Reading your sessions" };
    expect(stageWords(stages, "reading")).toBe("Reading your sessions");
    expect(stageWords(stages, "something_new")).toBe(w.unknownStage);
    expect(stageWords(stages, "toString")).toBe(w.unknownStage); // never a key from the object's prototype
    expect(stageWords(stages, null)).toBe(w.unknownStage);
  });
});

describe("a running job", () => {
  it("shows the stage and how far along it is", () => {
    render(<WorkProgress stage="Reading your sessions" done={3} total={10} unit="sessions" startedAt={T0 - 10} updatedAt={T0 - 2} state="running" />);
    expect(screen.getByText("Reading your sessions")).toBeTruthy();
    expect(screen.getByText(w.progress(3, 10, "sessions"), { exact: false })).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("max")).toBe("10");
    expect(bar.getAttribute("value")).toBe("3");
  });

  it("shows no bar or count when the API doesn't know how far along it is", () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 10} updatedAt={T0 - 2} state="running" />);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/ of /)).toBeNull();
  });

  it("counts the elapsed time up while the page is open, and the age of the last change", async () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 10} updatedAt={T0 - 8} state="running" />);
    expect(screen.getByText(w.running(w.duration(10)), { exact: false })).toBeTruthy();
    expect(screen.getByText(w.lastChange(w.duration(8)), { exact: false })).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(55_000);
    });
    expect(screen.getByText(w.running(w.duration(65)), { exact: false })).toBeTruthy();
    expect(screen.getByText(w.lastChange(w.duration(63)), { exact: false })).toBeTruthy();
  });

  it("says just now for a change in the last few seconds, and no change yet when there is none", () => {
    const view = render(<WorkProgress stage="Working" startedAt={T0 - 10} updatedAt={T0 - 1} state="running" />);
    expect(screen.getByText(w.justNow, { exact: false })).toBeTruthy();
    view.rerender(<WorkProgress stage="Working" startedAt={T0 - 10} state="running" />);
    expect(screen.getByText(w.noChangeYet, { exact: false })).toBeTruthy();
  });

  it("corrects a wrong clock on this computer with the server's own time", () => {
    // This browser thinks it is an hour later than the server does.
    render(<WorkProgress stage="Working" startedAt={T0 - 30 - 3600} updatedAt={T0 - 10 - 3600} serverNow={T0 - 3600} state="running" />);
    expect(screen.getByText(w.running(w.duration(30)), { exact: false })).toBeTruthy();
    expect(screen.getByText(w.lastChange(w.duration(10)), { exact: false })).toBeTruthy();
  });

  it("stops ticking when the page goes away", async () => {
    const view = render(<WorkProgress stage="Working" startedAt={T0} state="running" />);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("a stalled job", () => {
  it("says there has been no change for a while, that DMbot may be stuck, and what to do", () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 400} updatedAt={T0 - 190} state="stalled" />);
    expect(screen.getByText(w.stalled(w.duration(190)))).toBeTruthy();
    expect(screen.getByText(w.stalledDo)).toBeTruthy();
  });

  it("keeps the bar and says the quiet time once, not twice", () => {
    render(<WorkProgress stage="Working" done={3} total={10} startedAt={T0 - 400} updatedAt={T0 - 190} state="stalled" />);
    expect(screen.getByRole("progressbar")).toBeTruthy();
    expect(screen.queryByText(/Last progress/)).toBeNull();
  });

  it("uses the page's own advice when it has some", () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 400} updatedAt={T0 - 190} state="stalled" whatToDo="Open Import sessions again." />);
    expect(screen.getByText("Open Import sessions again.")).toBeTruthy();
    expect(screen.queryByText(w.stalledDo)).toBeNull();
  });
});

describe("a failed job", () => {
  it("says what happened and what to do next, and stops the clocks", () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 100} updatedAt={T0 - 5} state="failed" reason="DMbot could not read the file." />);
    expect(screen.getByRole("alert").textContent).toContain(w.failed);
    expect(screen.getByText("DMbot could not read the file.")).toBeTruthy();
    expect(screen.getByText(w.failedDo)).toBeTruthy();
    expect(screen.queryByText(w.running(w.duration(100)), { exact: false })).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("waiting for the person", () => {
  it("shows the stage only: no bar, no clock, no stall", () => {
    render(<WorkProgress stage="Waiting for your answers about 2 names" done={3} total={10} startedAt={T0 - 4000} updatedAt={T0 - 3900} state="running" waiting />);
    expect(screen.getByText("Waiting for your answers about 2 names")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/Running for|Last progress/)).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("a finished job", () => {
  it("says done and shows no clocks", () => {
    render(<WorkProgress stage="Working" startedAt={T0 - 100} updatedAt={T0 - 5} state="done" />);
    expect(screen.getByText(w.done)).toBeTruthy();
    expect(screen.queryByText(/Running for/)).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("for everyone", () => {
  it("has nothing serious or critical for axe", async () => {
    vi.useRealTimers();
    render(<WorkProgress stage="Reading your sessions" done={3} total={10} unit="sessions" startedAt={T0 - 10} updatedAt={T0 - 2} state="running" />);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

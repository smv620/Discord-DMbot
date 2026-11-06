/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The clock offer on an imported session (#3259): "Set it" or "Not now", a wait while the server answers, and the
// words for each answer. The switch is mocked so each test can turn the offer on or off; time is faked for the polls.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import ClockOffer from "../src/account/ClockOffer";
import { ApiError, type AccountApi, type ImportClock, type ImportProgress } from "../src/account/api";
import { clockWords as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/import-clock-switch", () => ({
  get IMPORT_CLOCK_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
  vi.useRealTimers();
});

const REF = "100.cmp1";
const FILE = "session-1.txt";
const offered: ImportClock = { state: "offered", label: "Day 5, evening" };

/** The import's progress, with this session's clock as the server answers it. */
const progressWith = (clock: ImportClock | null): ImportProgress => ({
  sessions: [{ file: FILE, date: "2026-09-14", state: "done", reason: null, clock }],
});

function stub(clockAnswer: () => Promise<void>, clockAnswerAfter: () => ImportClock = () => offered) {
  return {
    importClock: vi.fn(clockAnswer),
    importProgress: vi.fn(async () => progressWith(clockAnswerAfter())),
  } as unknown as AccountApi & { importClock: ReturnType<typeof vi.fn>; importProgress: ReturnType<typeof vi.fn> };
}

const passTime = async (ms: number): Promise<void> => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const press = (name: string): void => {
  fireEvent.click(screen.getByRole("button", { name }));
};

describe("the clock offer on an imported session", () => {
  it("shows nothing while the switch is off", () => {
    live.on = false;
    const api = stub(async () => undefined);
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    expect(screen.queryByRole("button", { name: w.setIt })).toBeNull();
  });

  it("shows nothing when there is no clock or it has expired", () => {
    const api = stub(async () => undefined);
    const { unmount } = render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={null} />);
    expect(screen.queryByRole("button", { name: w.setIt })).toBeNull();
    unmount();
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={{ state: "expired", label: "x" }} />);
    expect(screen.queryByRole("button", { name: w.setIt })).toBeNull();
  });

  it("offers Set it and Not now with the label", () => {
    render(<ClockOffer api={stub(async () => undefined)} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    expect(screen.getByText("Day 5, evening").tagName).toBe("STRONG");
    expect(screen.getByRole("button", { name: w.setIt })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.notNow })).toBeTruthy();
  });

  it("Set it sends the choice, shows the wait, then Clock set to the label", async () => {
    vi.useFakeTimers();
    const api = stub(async () => undefined, () => ({ state: "set", label: "Day 5, evening" }));
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    expect(api.importClock).toHaveBeenCalledWith(REF, "imp1", FILE, "set");
    expect((screen.getByRole("button", { name: w.setIt }) as HTMLButtonElement).disabled).toBe(true);
    await passTime(1000);
    expect(screen.getByText(w.setting(1))).toBeTruthy();
    await passTime(2000);
    expect(screen.getByText(w.set("Day 5, evening"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.setIt })).toBeNull();
  });

  it("Not now sends not-now and shows Not now.", async () => {
    vi.useFakeTimers();
    const api = stub(async () => undefined, () => ({ state: "dismissed", label: "Day 5, evening" }));
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.notNow);
    expect(api.importClock).toHaveBeenCalledWith(REF, "imp1", FILE, "not-now");
    await passTime(2000);
    expect(screen.getByText(w.dismissed)).toBeTruthy();
  });

  it("moved_on and split show their words", async () => {
    vi.useFakeTimers();
    const moved = stub(async () => undefined, () => ({ state: "moved_on", label: "Day 5, evening" }));
    const { unmount } = render(<ClockOffer api={moved} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    await passTime(2000);
    expect(screen.getByText(w.movedOn)).toBeTruthy();
    unmount();
    const split = stub(async () => undefined, () => ({ state: "split", label: "Day 5, evening" }));
    render(<ClockOffer api={split} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    await passTime(2000);
    expect(screen.getByText(w.split)).toBeTruthy();
  });

  it("30 seconds without a change shows the nothing-new line and brings the buttons back", async () => {
    vi.useFakeTimers();
    const api = stub(async () => undefined, () => offered);
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    await passTime(31_000);
    expect(screen.getByText(w.stalled(30))).toBeTruthy();
    expect((screen.getByRole("button", { name: w.setIt }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a network error shows its words and brings the buttons back", async () => {
    const failing = stub(async () => {
      throw new ApiError("network");
    });
    render(<ClockOffer api={failing} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    expect(await screen.findByText(w.error)).toBeTruthy();
    expect((screen.getByRole("button", { name: w.setIt }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a refused press says only the DM can set the clock", async () => {
    const refused = stub(async () => {
      throw new ApiError("not-allowed");
    });
    render(<ClockOffer api={refused} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    expect(await screen.findByText(w.notDm)).toBeTruthy();
  });

  it("Not now is disabled while the choice is sent", async () => {
    vi.useFakeTimers();
    const api = stub(async () => undefined, () => offered);
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    expect((screen.getByRole("button", { name: w.notNow }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a failure that lands after the look has started ends the asking: no more looks after it", async () => {
    vi.useFakeTimers();
    const api = stub(
      async () => {
        await new Promise((r) => setTimeout(r, 3000));
        throw new ApiError("network");
      },
      () => offered,
    );
    render(<ClockOffer api={api} campaignRef={REF} importId="imp1" file={FILE} clock={offered} />);
    press(w.setIt);
    await passTime(3500);
    expect(screen.getByText(w.error)).toBeTruthy();
    const looksAtFailure = api.importProgress.mock.calls.length;
    await passTime(10_000);
    expect(api.importProgress.mock.calls.length).toBe(looksAtFailure);
    expect(screen.queryByText(w.stalled(30))).toBeNull();
  });
});

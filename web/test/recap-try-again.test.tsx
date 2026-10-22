/** @jsxImportSource preact */
// @vitest-environment happy-dom
// After Try again is accepted the recap page shows only the working state, not the old failure
// beside it (#2333); a new failure brings the failure back with what is left.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { choiceWords } from "../src/content/recap-choices";
import { againLabel, text } from "../src/content/recaps";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;
const failed = (left: number): Recap => ({
  ...base,
  state: "failed",
  working: false,
  text: "",
  summary: "",
  housekeeping: [],
  canEdit: false,
  canPost: false,
  canTryAgain: left > 0,
  triesLeft: left,
  canDrop: true,
});
const running = (left: number): Recap => ({ ...failed(left), state: "working", working: true, canTryAgain: false, canDrop: false });

function open(first: Recap, ...reads: Recap[]) {
  const api = mockApi("table");
  const queue = [first, ...reads];
  let at = 0;
  api.recap = async () => queue[Math.min(at++, queue.length - 1)]!;
  // The API's answer to the press still carries the old failure (the case the owner saw).
  api.decideRecap = async () => ({ ...failed(first.triesLeft - 1), canTryAgain: first.triesLeft - 1 > 0 });
  render(<Recaps api={api} path={path} />);
  return api;
}

describe("Try again on a failed recap", () => {
  it("shows only the working state the moment it is accepted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    open(failed(1), failed(0));
    await screen.findByText(text.failed);
    fireEvent.click(screen.getByRole("button", { name: againLabel(1) }));
    expect(await screen.findByText(text.working)).toBeTruthy();
    expect(screen.queryByText(text.failed)).toBeNull();
    expect(screen.queryByRole("button", { name: /Try again/ })).toBeNull();
    expect(screen.queryByRole("button", { name: text.drop })).toBeNull();
    expect(screen.queryByText(text.noTriesLeft)).toBeNull();
    expect(screen.getByText("Being made")).toBeTruthy();
    expect(screen.queryByText("Couldn't be made")).toBeNull();
  });

  it("brings the failure back, with what is left, if the new run fails too", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    open(failed(1), running(0), failed(0));
    await screen.findByText(text.failed);
    fireEvent.click(screen.getByRole("button", { name: againLabel(1) }));
    await screen.findByText(text.working);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(await screen.findByText(text.failedNoTries)).toBeTruthy();
    const again = screen.getByRole("button", { name: text.again }) as HTMLButtonElement;
    expect(again.disabled).toBe(true);
    // A failed recap has nothing to edit or post, so the no-tries line must not say it has.
    expect(screen.getByText(text.failedNoTries)).toBeTruthy();
    expect(screen.queryByText(text.noTriesLeft)).toBeNull();
    expect(screen.queryByText(text.tryingAgain)).toBeNull();
    expect(screen.getByRole("button", { name: text.drop })).toBeTruthy();
  });
});

describe("a failed recap with no tries left", () => {
  it("says there are no more tries in the failure box, and never tells the DM to press Try again", async () => {
    open(failed(0));
    expect(await screen.findByText(text.failedNoTries)).toBeTruthy();
    expect(screen.queryByText(text.failed)).toBeNull();
    expect(screen.queryByText(/Press Try again/)).toBeNull();
    // Said once, not once in the box and once under the buttons.
    expect(screen.getAllByText(text.failedNoTries)).toHaveLength(1);
    expect((screen.getByRole("button", { name: text.again }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("still says 'Press Try again' while a try is left", async () => {
    open(failed(1));
    expect(await screen.findByText(text.failed)).toBeTruthy();
  });
});

describe("when the run ends", () => {
  it("gives Post and Edit back if it worked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    open(failed(1), { ...base, triesLeft: 0, canTryAgain: false });
    await screen.findByText(text.failed);
    fireEvent.click(screen.getByRole("button", { name: againLabel(1) }));
    await screen.findByText(text.working);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await screen.findByRole("button", { name: text.post })).toBeTruthy();
    expect(screen.getByRole("button", { name: text.edit })).toBeTruthy();
    expect(screen.queryByText(text.working)).toBeNull();
    expect(screen.queryByText(text.tryingAgain)).toBeNull();
  });

  it("shows a genuinely new failure inside the minute, once the tries have gone down", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    open(failed(1), failed(0));
    await screen.findByText(text.failed);
    fireEvent.click(screen.getByRole("button", { name: againLabel(1) }));
    await screen.findByText(text.working);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // The new failure used the last try, so the box says there are no more.
    expect(await screen.findByText(text.failedNoTries)).toBeTruthy();
  });

  it("ignores a read that still shows the old failure, then follows the real one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The API is a moment behind: the first poll still says "failed, one try left".
    open(failed(1), failed(1), { ...base, triesLeft: 0, canTryAgain: false });
    await screen.findByText(text.failed);
    fireEvent.click(screen.getByRole("button", { name: againLabel(1) }));
    await screen.findByText(text.working);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText(text.failed)).toBeNull();
    expect(screen.getByText(text.working)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await screen.findByRole("button", { name: text.post })).toBeTruthy();
  });
});

describe("a recap that is being made", () => {
  it("offers no Don't post and no 'tried twice' line until the run has ended", async () => {
    open({ ...running(0), triesLeft: 0 });
    await screen.findByText(text.working);
    expect(screen.queryByRole("button", { name: text.drop })).toBeNull();
    expect(screen.queryByText(text.noTriesLeft)).toBeNull();
  });
});

describe("the Settings words", () => {
  it("send the DM to the Main Menu in their DM Bot channel", () => {
    expect(choiceWords.sameOnDiscord).toBe(
      "The same choices are in Discord: 👤 DMbot Main Menu → ⚙️ Settings, in your DM Bot channel.",
    );
  });
});

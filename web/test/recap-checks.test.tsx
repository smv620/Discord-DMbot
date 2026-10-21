/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A recap that is written but waiting for its last check, and the checks' notes (#2297, #2280).
// The API's fields are `waitingForCheck` and `criticNotes`; the page shows them to the DM only.
import { act, cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { againLabel, statusLine, text } from "../src/content/recaps";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;
const waitingRecap = (over: Partial<Recap> = {}): Recap => ({
  ...base,
  working: true,
  waitingForCheck: true,
  text: "",
  summary: "",
  housekeeping: [],
  canEdit: false,
  canPost: false,
  canTryAgain: false,
  error: "📜 Your recap is written and waiting for one last check. DMbot will finish it as soon as it can.",
  ...over,
});

function pageWith(recaps: Recap[]): AccountApi {
  const api = mockApi("table");
  let at = 0;
  api.recap = async () => recaps[Math.min(at++, recaps.length - 1)]!;
  return api;
}

describe("a recap waiting for its last check", () => {
  it("says so in one steady line, not that DMbot is still writing", async () => {
    render(<Recaps api={pageWith([waitingRecap()])} path={path} />);
    expect(await screen.findByText(text.waitingForCheck)).toBeTruthy();
    expect(screen.queryByText(text.working)).toBeNull();
  });

  it("keeps the line through the gap where the API says something else, until it is ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const gap = waitingRecap({ waitingForCheck: false, error: "DMbot had a problem just now. It will try again." });
    const api = pageWith([waitingRecap(), gap, gap, waitingRecap(), { ...base, text: "The party crossed the river." }]);
    render(<Recaps api={api} path={path} />);
    expect(await screen.findByText(text.waitingForCheck)).toBeTruthy();
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(screen.getByText(text.waitingForCheck)).toBeTruthy();
      expect(screen.queryByText(text.working)).toBeNull();
      // The tag at the top agrees with the line below it, through the gap too.
      expect(screen.queryByText("Being made")).toBeNull();
      expect(screen.getByText("Waiting for a last check")).toBeTruthy();
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(await screen.findByText("The party crossed the river.")).toBeTruthy();
    expect(screen.queryByText(text.waitingForCheck)).toBeNull();
  });

  it("goes back to the plain line for a recap that was never waiting", async () => {
    render(<Recaps api={pageWith([waitingRecap({ waitingForCheck: false, error: "" })])} path={path} />);
    expect(await screen.findByText(text.working)).toBeTruthy();
    expect(screen.queryByText(text.waitingForCheck)).toBeNull();
  });

  it("drops the line when the recap has failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed = waitingRecap({ waitingForCheck: false, working: false, state: "failed", canTryAgain: true });
    render(<Recaps api={pageWith([waitingRecap(), failed])} path={path} />);
    expect(await screen.findByText(text.waitingForCheck)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await screen.findByText(text.failed)).toBeTruthy();
    expect(screen.queryByText(text.waitingForCheck)).toBeNull();
  });

  it("forgets the wait after a failure: Try again and 'still writing' shows the plain line", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed = waitingRecap({ waitingForCheck: false, working: false, state: "failed", canTryAgain: true, triesLeft: 2 });
    const again = waitingRecap({ waitingForCheck: false, error: "" });
    const api = pageWith([waitingRecap(), failed]);
    api.decideRecap = async () => again;
    render(<Recaps api={api} path={path} />);
    expect(await screen.findByText(text.waitingForCheck)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await screen.findByText(text.failed)).toBeTruthy();
    expect(screen.queryByText(text.waitingForCheck)).toBeNull();
    // Try again: the recap is being made again, and nothing says it is waiting for a check.
    await act(async () => {
      screen.getByRole("button", { name: againLabel(2) }).click();
    });
    expect(await screen.findByText(text.working)).toBeTruthy();
    expect(screen.queryByText(text.waitingForCheck)).toBeNull();
    expect(screen.getByText("Being made")).toBeTruthy();
  });

  it("is named in the list", () => {
    expect(statusLine(waitingRecap())).toBe("Waiting for a last check");
    expect(statusLine(waitingRecap({ waitingForCheck: false }))).toBe("Being made");
  });
});

describe("the checks' notes", () => {
  const notes = ["Check: Mira's reason for the lie isn't in the play.", "Check: the style could not be checked this time."];

  it("sit under a closed 'What the checks said', for the DM", async () => {
    render(<Recaps api={pageWith([{ ...base, criticNotes: notes }])} path={path} />);
    const summary = await screen.findByText(text.checksHeading);
    const details = summary.closest("details")!;
    expect(details.hasAttribute("open")).toBe(false);
    for (const note of notes) expect(details.textContent).toContain(note);
    expect(details.textContent).toContain(text.checksNote);
  });

  it("are left out when there are none, or the API doesn't send them", async () => {
    render(<Recaps api={pageWith([{ ...base, criticNotes: [] }])} path={path} />);
    await screen.findByText(base.title);
    expect(screen.queryByText(text.checksHeading)).toBeNull();
    cleanup();
    render(<Recaps api={pageWith([base])} path={path} />);
    await screen.findByText(base.title);
    expect(screen.queryByText(text.checksHeading)).toBeNull();
  });
});

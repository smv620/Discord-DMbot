/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "After the session: things to check" (#2661; STORY_MEMORY 9a), against the shapes agreed on the issue:
// the counts, minor contradictions with both statements and their evidence (a placeholder where the words
// are gone), what the party has wrong grouped as disguised, misunderstood and false, the four answers
// (queued, with Undo), the wait panel while the report is made, and nothing at all for anyone but the DM.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type SessionReport } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapReport, { REPORT_WATCH_LIMIT_MS } from "../src/account/RecapReport";
import { reportWords as w } from "../src/content/report-words";

const AT = 1791000000;

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function open(campaign = "100.cmp1", api: AccountApi = mockApi("table")) {
  render(<RecapReport api={api} campaignRef={campaign} sessionAt={AT} />);
  return api;
}
const heading = () => screen.findByRole("heading", { name: w.heading });
const button = (name: string | RegExp) => screen.getByRole("button", { name });

describe("the report", () => {
  it("shows the counts, the contradictions with both statements, and the party's differences in three groups", async () => {
    await open();
    await screen.findByText(/2 that may not fit, 3 the party has wrong\. 5 still wait for you\./);
    expect(screen.getByRole("heading", { name: w.contradictionsHeading })).toBeTruthy();
    expect(screen.getByText("The ferryman is dead.")).toBeTruthy();
    expect(screen.getByText("The ferryman rowed the party across the river.")).toBeTruthy();
    for (const g of ["disguised", "misunderstood", "false"]) {
      expect(screen.getByRole("heading", { name: w.groups[g] as string })).toBeTruthy();
    }
    expect(screen.getByText(/Aldric is loyal to the Lantern Circle\./)).toBeTruthy();
    expect(screen.getByText(/Aldric serves the Ashen Crown\./)).toBeTruthy();
  });

  it("says when the game times are not known, and when it was a reminder", async () => {
    await open();
    await screen.findByText(w.gameTimesUnknown);
    expect(screen.getByText(w.reminded(3))).toBeTruthy();
  });

  it("writes a placeholder where the words are gone, never the words or who said them", async () => {
    await open();
    await screen.findAllByText(/no longer available/);
    expect(document.body.textContent).toContain("a line from session 4, no longer available");
    expect(document.body.textContent).toContain("a fact from session 3: “found him in the reeds”");
  });

  it("says that nothing changed by itself and has no player in it", async () => {
    await open();
    await heading();
    expect(screen.getByText(/you decide what each one means/i)).toBeTruthy();
  });

  it("says there is nothing to check when the report is empty", async () => {
    await open("100.cmp1.report-empty");
    await screen.findByText(w.empty);
  });
});

describe("who sees it", () => {
  it("shows nothing at all to a player (the API says not allowed)", async () => {
    await open("100.cmp1.player");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(document.body.textContent).toBe("");
  });

  it("shows nothing when the API has no report routes (an older API)", async () => {
    const api = mockApi("table");
    delete api.sessionReport;
    await open("100.cmp1", api);
    expect(document.body.textContent).toBe("");
  });
});

describe("the answers", () => {
  it("offers the four answers on a difference with a connection to open, and three on one without", async () => {
    await open();
    await screen.findByText(/Aldric is loyal/);
    expect(screen.getAllByRole("button", { name: /^That's intended/ })).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: /^Fix the DM's view/ })).toHaveLength(2); // only where there is a connection to open
    expect(screen.getAllByRole("button", { name: /^Not a problem/ })).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: /^Remind me next session/ })).toHaveLength(5);
  });

  it("saves That's intended, shows it and the count, and Undo puts it back", async () => {
    const api = await open();
    await screen.findByText(/Aldric is loyal/);
    fireEvent.click(button(/^That's intended: Aldric is loyal/));
    await screen.findByText(w.answered("intended"));
    expect(screen.getByText(/4 still wait for you\./)).toBeTruthy();
    expect((api as { calls?: string[] }).calls).toBeDefined();
    fireEvent.click(button(/^Undo the answer: Aldric is loyal/));
    await waitFor(() => expect(screen.queryByText(w.answered("intended"))).toBeNull());
    expect(screen.getByText(/5 still wait for you\./)).toBeTruthy();
  });

  it("opens the Story graph after Fix the DM's view, without claiming it is fixed", async () => {
    await open();
    await screen.findByText(/Aldric is loyal/);
    fireEvent.click(button(/^Fix the DM's view: Aldric is loyal/));
    await screen.findByText(w.answered("fix"));
    const link = screen.getByRole("link", { name: w.openConnection }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/account/web/100.cmp1");
    expect(document.body.textContent).not.toContain(w.answered("fixed"));
  });

  it("says what happened when the bot cannot save, and keeps the item open", async () => {
    await open();
    await screen.findByText(/shrine key opens the keep/);
    fireEvent.click(button(/^Fix the DM's view: The shrine key/));
    await screen.findByText(w.errors["cannot-write"] as string);
    expect(screen.getByRole("button", { name: /^That's intended: The shrine key/ })).toBeTruthy();
  });

  it("reads the list again when the item was already answered, and says so", async () => {
    const api = mockApi("table");
    const real = api.answerReport!;
    api.answerReport = async (...args) => {
      await real(args[0], args[1], args[2], "intended"); // answered elsewhere first
      return real(...args);
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric is loyal/);
    fireEvent.click(button(/^Not a problem: Aldric is loyal/));
    await screen.findByText(w.errors["gone"] as string);
    await screen.findByText(w.answered("intended")); // the list now shows the answer that was already there
  });

  it("gives an unknown error the general sentence, never the code", async () => {
    const api = mockApi("table");
    api.answerReport = async () => {
      throw new ApiError("slow-down");
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric is loyal/);
    fireEvent.click(button(/^Not a problem: Aldric is loyal/));
    await screen.findByText(w.errors["server"] as string);
    expect(document.body.textContent).not.toMatch(/slow-down/);
  });

  it("does not let two answers run at once", async () => {
    const api = mockApi("table");
    let release: () => void = () => undefined;
    api.answerReport = () => new Promise<void>((r) => (release = r));
    await open("100.cmp1", api);
    await screen.findByText(/Aldric is loyal/);
    fireEvent.click(button(/^Not a problem: Aldric is loyal/));
    await screen.findByText(w.working);
    expect((button(/^That's intended: The shrine key/) as HTMLButtonElement).disabled).toBe(true);
    release();
  });
});

describe("making the report", () => {
  it("offers Make the report, then shows the wait panel with the stage and how far", async () => {
    await open("100.cmp1.report-none");
    await screen.findByText(w.none);
    fireEvent.click(button(w.make));
    await screen.findByText(w.stages["reading"] as string);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    await screen.findByText(w.stages["grading"] as string);
    expect(document.body.textContent).toMatch(/2 of 5 items/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    await screen.findByText(/2 that may not fit/);
  });

  it("says what happened when it fails and offers to try again", async () => {
    await open("100.cmp1.report-failed");
    fireEvent.click(await screen.findByRole("button", { name: w.make }));
    await screen.findByText(w.failed, { exact: false });
    fireEvent.click(button(w.makeAgain));
    await screen.findByText(w.stages["reading"] as string); // making again; the first look comes after the retry is sent
    await act(async () => {
      await vi.advanceTimersByTimeAsync(7000);
    });
    await screen.findByText(/2 that may not fit/);
  });

  it("stops looking after 30 minutes and says so", async () => {
    const api = mockApi("table");
    const stuck: SessionReport = {
      session: { at: AT },
      state: "making",
      progress: { stage: "grading", done: 1, total: 5, unit: "items", startedAt: 1, updatedAt: 2, serverNow: 3, state: "running" },
      counts: { contradictions: 0, disguised: 0, misunderstood: 0, false: 0, open: 0, answered: 0 },
      contradictions: [],
      differences: { disguised: [], misunderstood: [], false: [] },
    };
    api.sessionReport = async () => stuck;
    await open("100.cmp1", api);
    await screen.findByText(w.stages["grading"] as string);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REPORT_WATCH_LIMIT_MS + 60_000);
    });
    await screen.findByText(w.stoppedWatching);
  });
});

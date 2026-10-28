/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "After the session: things to check" (#2661; STORY_MEMORY 9a), against the shapes agreed on the issue:
// the counts, minor contradictions with both statements and their evidence (a placeholder where the words
// are gone), what the party has wrong grouped as disguised, misunderstood and false, the four answers
// (queued, with Undo), the wait panel while the report is made, and nothing at all for anyone but the DM.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type SessionReport } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapReport, { REPORT_WATCH_LIMIT_MS } from "../src/account/RecapReport";
import { reportWords as w } from "../src/content/report-words";

const AT = 1791000000;

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function open(campaign = "100.cmp1", api: ReturnType<typeof mockApi> = mockApi("table")) {
  render(<RecapReport api={api} campaignRef={campaign} sessionAt={AT} />);
  return api;
}
const heading = () => screen.findByRole("heading", { name: w.heading });
/** The item (a list row) that holds some words, so a button is found in the right one. */
const item = (words: RegExp): HTMLElement => screen.getByText(words).closest("li") as HTMLElement;
const within1 = (words: RegExp, name: string | RegExp): HTMLElement => {
  const found = Array.from(item(words).querySelectorAll("button")).find((b) => (typeof name === "string" ? b.textContent === name : name.test(b.textContent ?? "")));
  if (!found) throw new Error(`no button ${String(name)} in the item`);
  return found;
};

describe("the report", () => {
  it("shows the counts, the contradictions with both statements, and the party's differences in three groups", async () => {
    await open();
    await screen.findByText(/2 that may not fit, 3 where the party sees it differently\. 5 left for you to answer\./);
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

  it("says that nothing changed by itself and that DMbot may be wrong", async () => {
    await open();
    await heading();
    expect(screen.getByText(/DMbot may be wrong/)).toBeTruthy();
  });

  it("never quotes a transcript line, even when the API sends one that still exists", async () => {
    const api = mockApi("table");
    const real = api.sessionReport!;
    api.sessionReport = async (...args) => {
      const got = await real(...args);
      got.contradictions[0]!.first.evidence = [{ kind: "line", session: 6, available: true, quote: "THE WORDS A PLAYER SAID" }];
      return got;
    };
    await open("100.cmp1", api);
    await screen.findByText(/The ferryman is dead/);
    expect(document.body.textContent).toContain("a line from session 6");
    expect(document.body.textContent).not.toContain("THE WORDS A PLAYER SAID");
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
    await screen.findByText(/Aldric has hidden it/);
    expect(screen.getAllByRole("button", { name: "That's intended" })).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: "Fix the DM's view" })).toHaveLength(2); // only where there is a connection to open
    expect(screen.getAllByRole("button", { name: "Not a problem" })).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: "Remind me next session" })).toHaveLength(5);
  });

  it("gives all four answers the same weight and ties each to its item for a screen reader", async () => {
    await open();
    await screen.findByText(/Aldric has hidden it/);
    const own = item(/Aldric has hidden it/);
    for (const b of Array.from(own.querySelectorAll("button"))) {
      expect(b.className).toBe("button secondary");
      expect(own.querySelector(`#${b.getAttribute("aria-describedby")}`)).toBeTruthy();
      expect(b.getAttribute("aria-label")).toBeNull(); // (the visible words are the name)
    }
  });

  it("saves That's intended, says so once, shows the count, and Undo puts it back", async () => {
    const api = await open();
    await screen.findByText(/Aldric has hidden it/);
    fireEvent.click(within1(/Aldric has hidden it/, "That's intended"));
    await screen.findByText(w.answered("intended"));
    expect(api.calls).toContain("answerReport:100.cmp1:1791000000:d-lie:intended");
    expect(screen.getByText(/4 left for you to answer\./)).toBeTruthy();
    expect(document.querySelector("[role=status]")?.textContent).toBe("Saved. 4 left."); // one polite place
    expect(document.querySelectorAll("[role=status]")).toHaveLength(1);
    fireEvent.click(within1(/Aldric has hidden it/, "Undo"));
    await waitFor(() => expect(screen.queryByText(w.answered("intended"))).toBeNull());
    expect(api.calls).toContain("answerReport:100.cmp1:1791000000:d-lie:undo");
    expect(screen.getByText(/5 left for you to answer\./)).toBeTruthy();
  });

  it("keeps the keyboard where it was: on Undo after an answer", async () => {
    await open();
    await screen.findByText(/Aldric has hidden it/);
    fireEvent.click(within1(/Aldric has hidden it/, "Not a problem"));
    await screen.findByText(w.answered("not_a_problem"));
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Undo"));
  });

  it("opens the Story graph after Fix the DM's view, without claiming it is fixed", async () => {
    await open();
    await screen.findByText(/Aldric has hidden it/);
    fireEvent.click(within1(/Aldric has hidden it/, "Fix the DM's view"));
    await screen.findByText(w.answered("fix"));
    const link = screen.getByRole("link", { name: w.openConnection }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/account/web/100.cmp1");
    expect(document.body.textContent).not.toContain(w.answered("fixed"));
    expect(w.answered("fix")).toContain("Nothing has changed yet");
  });

  it("says what happened when the bot cannot save, and keeps the item open", async () => {
    await open();
    await screen.findByText(/They saw it fit a lock/);
    fireEvent.click(within1(/They saw it fit a lock/, "Fix the DM's view"));
    await screen.findByText(w.errors["cannot-write"] as string);
    expect(within1(/They saw it fit a lock/, "That's intended")).toBeTruthy();
  });

  it("reads the list again when the item was already answered, and says so", async () => {
    const api = mockApi("table");
    const real = api.answerReport!;
    api.answerReport = async (...args) => {
      await real(args[0], args[1], args[2], "intended"); // answered elsewhere first
      return real(...args);
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric has hidden it/);
    fireEvent.click(within1(/Aldric has hidden it/, "Not a problem"));
    await screen.findByText(w.errors["gone"] as string);
    await screen.findByText(w.answered("intended")); // the list now shows the answer that was already there
  });

  it("gives an unknown error the general sentence, never the code", async () => {
    const api = mockApi("table");
    api.answerReport = async () => {
      throw new ApiError("slow-down");
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric has hidden it/);
    fireEvent.click(within1(/Aldric has hidden it/, "Not a problem"));
    await screen.findByText(w.errors["server"] as string);
    expect(document.body.textContent).not.toMatch(/slow-down/);
  });

  it("sends one answer even when the button is pressed twice in one tick", async () => {
    const api = mockApi("table");
    let sent = 0;
    let release: () => void = () => undefined;
    api.answerReport = () => {
      sent += 1;
      return new Promise<void>((r) => (release = r));
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric has hidden it/);
    const b = within1(/Aldric has hidden it/, "Not a problem");
    fireEvent.click(b);
    fireEvent.click(b);
    await screen.findByText(w.working);
    expect(sent).toBe(1);
    expect((within1(/They saw it fit a lock/, "That's intended") as HTMLButtonElement).disabled).toBe(true);
    release();
  });
});

describe("when something goes wrong or changes", () => {
  it("keeps looking after one failed look while it is being made, and clears the error when a look works", async () => {
    const api = mockApi("table");
    const real = api.sessionReport!;
    let looks = 0;
    api.sessionReport = async (...args) => {
      looks += 1;
      if (looks === 2) throw new ApiError("network"); // one blip, in the middle of the wait
      return real(...args);
    };
    await open("100.cmp1.report-none", api);
    fireEvent.click(await screen.findByRole("button", { name: w.make }));
    await screen.findByText(w.stages["reading"] as string);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    await screen.findByText(w.errors["network"] as string);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14000); // (the retry waits longer, then the look after it works)
    });
    await screen.findByText(/2 that may not fit/);
    expect(screen.queryByText(w.errors["network"] as string)).toBeNull();
  });

  it("shows nothing of the old session when it moves to another, and the heading never appears first for anyone else", async () => {
    const api = mockApi("table");
    const { rerender } = render(<RecapReport api={api} campaignRef="100.cmp1" sessionAt={AT} />);
    await screen.findByText(/Aldric has hidden it/);
    rerender(<RecapReport api={api} campaignRef="100.cmp1.player" sessionAt={AT} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(document.body.textContent).toBe("");
  });

  it("shows no heading before the API has said it is a DM's report", async () => {
    const api = mockApi("table");
    let release: (r: SessionReport) => void = () => undefined;
    api.sessionReport = () => new Promise<SessionReport>((r) => (release = r));
    await open("100.cmp1", api);
    expect(document.body.textContent).toBe("");
    release({
      session: { at: AT },
      state: "none",
      counts: { contradictions: 0, disguised: 0, misunderstood: 0, false: 0, open: 0, answered: 0 },
      contradictions: [],
      differences: { disguised: [], misunderstood: [], false: [] },
    });
    await heading();
  });

  it("decides 'nothing to check' from the lists, not from the counts", async () => {
    const api = mockApi("table");
    const real = api.sessionReport!;
    api.sessionReport = async (...args) => {
      const got = await real(...args);
      got.counts = { contradictions: 0, disguised: 0, misunderstood: 0, false: 0, open: 0, answered: 0 };
      return got;
    };
    await open("100.cmp1", api);
    await screen.findByText(/Aldric has hidden it/);
    expect(screen.queryByText(w.empty)).toBeNull();
  });
});

describe("making the report", () => {
  it("offers Make the report, then shows the wait panel with the stage and how far", async () => {
    await open("100.cmp1.report-none");
    await screen.findByText(w.none);
    fireEvent.click(screen.getByRole("button", { name: w.make }));
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
    expect(screen.getByRole("link", { name: w.sayHello }).getAttribute("href")).toBe("/hello");
    fireEvent.click(screen.getByRole("button", { name: w.makeAgain }));
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

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Heads-ups from this session" (#3695, page half 1): five at a time, in Discord's words; Story changed and Wasn't real are answered
// on the page and the panel asks and reads again; Ignore writes nothing; nothing is asked while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import Continuity from "../src/account/Continuity";
import { mockApi } from "../src/account/mock";
import { continuityWords as w } from "../src/content/continuity-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/continuity-switch", () => ({
  get CONTINUITY_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "200000000000000001.cmp1";
const C1 = "c1".repeat(16);

function panel(api: AccountApi) {
  return render(<Continuity api={api} refId={REF} />);
}

describe("the heads-ups panel", () => {
  it("shows the header, five heads-ups at a time, and how many are waiting", async () => {
    const view = panel(mockApi("table"));
    expect(await screen.findByText(w.heading)).toBeTruthy();
    expect(view.container.querySelectorAll("li").length).toBe(5);
    expect(screen.getByText(w.more(1))).toBeTruthy();
  });

  it("writes the clash line in Discord's words, with its level, its session, its quote and what is at the table", async () => {
    const view = panel(mockApi("table"));
    await screen.findByText(w.heading);
    const text = view.container.textContent ?? "";
    expect(text).toContain("⚠️ Probable: Captain Brask. Before in Session 2: Brask is in the keep (“Brask was in the keep”). At the table: Captain Brask is at the ferry.");
  });

  it("writes the ended line for a death, and the pin line with the pin sentence in the header", async () => {
    const view = panel(mockApi("table"));
    await screen.findByText(w.heading);
    const text = view.container.textContent ?? "";
    expect(text).toContain("⚠️ Likely: Old Pell died in Session 1 (“Pell died at the mill”). At the table: Old Pell is alive.");
    expect(text).toContain("📌 Check: Tamsin Reed. You pinned: Tamsin lives in the hills.");
    expect(text).toContain(w.pinNote.trim());
  });

  it("writes the secret line with the learner and the list, or that the list is empty", async () => {
    const view = panel(mockApi("table"));
    await screen.findByText(w.heading);
    const text = view.container.textContent ?? "";
    expect(text).toContain("⚠️ Check: Brewner spoke of the cellar key and isn't on its list (Mira Voss).");
    expect(text).toContain("⚠️ Check: Someone spoke of the mill plan and isn't on its list (the list is empty).");
  });

  it("the first button says the heads-up's own choice", async () => {
    panel(mockApi("table"));
    const first = await screen.findByRole("button", { name: w.storyChanged });
    expect(first).toBeTruthy();
    expect(screen.getByRole("button", { name: w.stillHere })).toBeTruthy();
  });

  it("Story changed sends the change, then asks and reads again", async () => {
    const api = mockApi("table");
    panel(api);
    fireEvent.click(await screen.findByRole("button", { name: w.storyChanged }));
    await waitFor(() => expect(api.calls).toContain(`answerContinuity:${REF}:${C1}:changed`));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("askContinuity")).length).toBeGreaterThan(1));
  });

  it("Wasn't real sends the not, and the line leaves the page", async () => {
    const api = mockApi("table");
    const view = panel(api);
    fireEvent.click((await screen.findAllByRole("button", { name: w.wasnt }))[0]!);
    await waitFor(() => expect(api.calls).toContain(`answerContinuity:${REF}:${C1}:not`));
    await waitFor(() => expect(view.container.textContent).not.toContain("Captain Brask"));
  });

  it("Ignore hides the line and writes nothing; it still counts as waiting", async () => {
    const api = mockApi("table");
    const view = panel(api);
    await screen.findByText(w.heading);
    fireEvent.click((await screen.findAllByRole("button", { name: w.ignore }))[0]!);
    expect(view.container.textContent).not.toContain("Captain Brask");
    expect(api.calls.some((c) => c.startsWith("answerContinuity"))).toBe(false);
    expect(screen.getByText(w.more(1))).toBeTruthy();
  });

  it("a gone heads-up says it is already settled", async () => {
    const api = mockApi("table");
    api.answerContinuity = async () => {
      throw new ApiError("gone");
    };
    panel(api);
    fireEvent.click(await screen.findByRole("button", { name: w.storyChanged }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
  });

  it("a refused heads-up says it is still waiting", async () => {
    const api = mockApi("table");
    api.answerContinuity = async () => {
      throw new ApiError("refused");
    };
    panel(api);
    fireEvent.click((await screen.findAllByRole("button", { name: w.wasnt }))[0]!);
    expect(await screen.findByText(w.refused)).toBeTruthy();
  });

  it("shows the nothing-left line when none is waiting", async () => {
    const api = mockApi("table");
    api.continuityHits = async () => ({ total: 0, hits: [] });
    panel(api);
    expect(await screen.findByText(w.nothingLeft)).toBeTruthy();
  });

  it("makes no call while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    panel(api);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.calls.some((c) => c.startsWith("continuityHits") || c.startsWith("askContinuity"))).toBe(false);
  });

  it("ignores a second press while an answer is with DMbot", async () => {
    const api = mockApi("table");
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    let asked = 0;
    api.answerContinuity = async () => {
      asked += 1;
      await gate;
    };
    panel(api);
    const first = (await screen.findAllByRole("button", { name: w.wasnt }))[0]!;
    fireEvent.click(first);
    fireEvent.click(first);
    expect(asked).toBe(1);
    release();
  });

  it("a \"They learned it\" card's first button sends the change and says they learned it", async () => {
    const api = mockApi("table");
    panel(api);
    const learned = (await screen.findAllByRole("button", { name: w.theyLearned }))[0]!;
    fireEvent.click(learned);
    await waitFor(() => expect(screen.getByText(w.learned_done)).toBeTruthy());
    expect(api.calls.some((c) => c.startsWith("answerContinuity:"))).toBe(true);
  });
});

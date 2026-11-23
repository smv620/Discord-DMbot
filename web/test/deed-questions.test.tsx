/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Deeds to record" (#3591, page half): one deed at a time, in Discord's words. Record, Not this, Later and Undo are answered on the page;
// nothing is read while the switch is off, and a second press waits for the first answer.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import DeedQuestions from "../src/account/DeedQuestions";
import { mockApi } from "../src/account/mock";
import { deedWords as w } from "../src/content/deed-questions-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/deed-questions-switch", () => ({
  get DEED_QUESTIONS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "200000000000000001.cmp1";
const MIRA = "a1".repeat(16);

function card(api: AccountApi) {
  const onLeft = vi.fn();
  const view = render(<DeedQuestions api={api} campaignRef={REF} id="deeds" onLeft={onLeft} />);
  return { view, onLeft };
}

describe("the deeds card", () => {
  it("shows the first deed with its kind, who it mattered to and how it landed", async () => {
    const { view } = card(mockApi("table"));
    const text = (await screen.findByText(/Mira Voss/)).closest("div")!.textContent ?? "";
    expect(text).toContain("Mira Voss: “pulled Tavin out of the river” (a rescue)");
    expect(view.container.textContent).toContain("It mattered to Tavin (it helped them). Record it?");
  });

  it("says how many are after this one", async () => {
    card(mockApi("table"));
    expect(await screen.findByText(w.more(1))).toBeTruthy();
  });

  it("Record records it, shows Recorded and an Undo", async () => {
    const api = mockApi("table");
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.record }));
    expect(await screen.findByText(w.recorded)).toBeTruthy();
    expect(api.calls).toContain(`answerDeed:${REF}:${MIRA}:record`);
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("Undo takes the recorded deed back", async () => {
    const api = mockApi("table");
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.record }));
    // The Undo is pressable once the record's own read has finished (the press is then no longer with DMbot).
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("answerDeed:") && c.includes(":undo:"))).toBe(true);
  });

  it("Not this turns it down and shows the next deed", async () => {
    const api = mockApi("table");
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.not }));
    expect(await screen.findByText(w.notThis)).toBeTruthy();
    expect(api.calls).toContain(`answerDeed:${REF}:${MIRA}:not`);
  });

  it("Later hides the deed, and the next one shows", async () => {
    const { view } = card(mockApi("table"));
    fireEvent.click(await screen.findByRole("button", { name: w.later }));
    await waitFor(() => expect(view.container.textContent).toContain("Captain Brask"));
    expect(view.container.textContent).not.toContain("Mira Voss");
  });

  it("a gone deed says it was already answered", async () => {
    const api = mockApi("table");
    api.answerDeed = async () => {
      throw new ApiError("gone");
    };
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.record }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
  });

  it("a too-late Undo says so", async () => {
    const api = mockApi("table");
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.record }));
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    api.answerDeed = async () => {
      throw new ApiError("undo-late");
    };
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.tooLate)).toBeTruthy();
  });

  it("a refused answer says a name may be unknown", async () => {
    const api = mockApi("table");
    api.answerDeed = async () => {
      throw new ApiError("refused");
    };
    card(api);
    fireEvent.click(await screen.findByRole("button", { name: w.record }));
    expect(await screen.findByText(w.refused)).toBeTruthy();
  });

  it("shows the empty line when no deed is waiting", async () => {
    const api = mockApi("table");
    api.deedQuestions = async () => ({ total: 0, deeds: [] });
    card(api);
    expect(await screen.findByText(w.empty)).toBeTruthy();
  });

  it("makes no call while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    card(api);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.calls.some((c) => c.startsWith("deedQuestions"))).toBe(false);
    expect(screen.queryByRole("button", { name: w.record })).toBeNull();
  });

  it("ignores a second press while an answer is with DMbot", async () => {
    const api = mockApi("table");
    let release!: () => void;
    const gate = new Promise<void>((go) => {
      release = go;
    });
    const answer = api.answerDeed!.bind(api);
    let asked = 0;
    api.answerDeed = async (ref, change) => {
      asked += 1;
      await gate;
      return answer(ref, change);
    };
    card(api);
    const record = await screen.findByRole("button", { name: w.record });
    fireEvent.click(record);
    fireEvent.click(record);
    expect(asked).toBe(1);
    release();
    await screen.findByText(w.recorded);
  });

  it("the words of the deed are not in the document title", async () => {
    card(mockApi("table"));
    await screen.findByText(/Mira Voss/);
    expect(document.title).not.toContain("pulled Tavin");
  });
});

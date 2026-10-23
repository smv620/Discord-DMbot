/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Plot check on a recap's page (#2124): PlotBot's questions, the four answers, the chat with Save and
// Not that, and the words Approve waits on. It shows only while PLOT_CHECK_LIVE is true (false today).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import RecapPlotCheck from "../src/account/RecapPlotCheck";
import { plotWords as w } from "../src/content/recap-plot";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get PLOT_CHECK_LIVE() {
      return live.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const draft = mockRecaps()[0]!;
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

async function open(text = draft.text) {
  live.on = true;
  const api = mockApi("table");
  const blocked = vi.fn();
  const fix = vi.fn();
  const view = render(<RecapPlotCheck api={api} refId={draft.ref} text={text} onBlocked={blocked} onFix={fix} />);
  await screen.findByRole("heading", { name: w.heading });
  return { api, blocked, fix, view };
}
const issue = (n: number) => screen.getAllByRole("group")[n - 1]!;

describe("the plot check", () => {
  it("shows nothing while it is switched off", async () => {
    const { container } = render(<RecapPlotCheck api={mockApi("table")} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("lists each question with the recap's sentence and the earlier note, and Approve waits", async () => {
    const { blocked } = await open();
    expect(within(issue(1)).getByText("Ulfgar opened the gate.")).toBeTruthy();
    expect(within(issue(1)).getByText(/Ulfgar fell to the ghoul/)).toBeTruthy();
    expect(within(issue(1)).getByText(/Earlier, session 6:/)).toBeTruthy();
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(w.waitingOn(2)));
  });

  it("Leave it as is answers the question, and Approve opens once every question has an answer", async () => {
    const { api, blocked } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.leave, 1) }));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(w.waitingOn(1)));
    expect(calls(api, "answerPlotIssue")[0]).toContain('"kind":"leave"');
    expect(within(issue(1)).getByText(w.yourAnswer.leave!)).toBeTruthy();
    fireEvent.click(within(issue(2)).getByRole("button", { name: w.answerFor(w.answers.leave, 2) }));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(null));
  });

  it("Not a problem keeps the DM's note", async () => {
    const { api } = await open();
    fireEvent.click(within(issue(2)).getByRole("button", { name: w.answerFor(w.answers.fine, 2) }));
    fireEvent.input(within(issue(2)).getByLabelText(w.noteLabel), { target: { value: "Mara took it back." } });
    fireEvent.click(within(issue(2)).getByRole("button", { name: `${w.noteSave}, question 2` }));
    await waitFor(() => expect(calls(api, "answerPlotIssue")).toHaveLength(1));
    expect(calls(api, "answerPlotIssue")[0]).toContain('"kind":"fine","note":"Mara took it back."');
  });

  it("Fix the recap sends the words to the fix box and records the answer", async () => {
    const { api, fix } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.fix, 1) }));
    expect(fix).toHaveBeenCalledWith(w.fixPrefill("Ulfgar died in session 6.", "Ulfgar opened the gate."));
    await waitFor(() => expect(calls(api, "answerPlotIssue")[0]).toContain('"kind":"fix"'));
  });

  it("saves a suggested note only on Save, and Not that saves nothing", async () => {
    const { api, blocked } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He was raised." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    expect(await within(issue(1)).findByText(/Ulfgar: alive again from session 7/)).toBeTruthy();
    expect(calls(api, "decidePlotProposal")).toHaveLength(0); // nothing saved by asking
    fireEvent.click(within(issue(1)).getByRole("button", { name: `${w.notThat}: Ulfgar: alive again from session 7` }));
    expect(await within(issue(1)).findByText(w.proposalDeclined)).toBeTruthy();
    expect(calls(api, "decidePlotProposal")[0]).toContain(":decline");
    expect(blocked).not.toHaveBeenLastCalledWith(null);
    // a second message, saved this time: the question is answered "the story changed"
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He really was." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const saves = await within(issue(1)).findAllByRole("button", { name: new RegExp(`^${w.save}:`) });
    fireEvent.click(saves[0]!);
    await waitFor(() => expect(within(issue(1)).getByText(w.yourAnswer.changed!)).toBeTruthy());
    expect(calls(api, "decidePlotProposal")[1]).toContain(":save");
    void blocked;
  });

  it("says Checking the change… after an edit, then shows the new questions", async () => {
    const { view, api } = await open("one");
    await api.saveRecap!(draft.ref, "A new version of the recap.", []); // the DM's edit, as the page sends it
    view.rerender(<RecapPlotCheck api={api} refId={draft.ref} text="two" onBlocked={() => {}} onFix={() => {}} />);
    expect(await screen.findByText(w.checkingChange)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(w.checkingChange)).toBeNull(), { timeout: 8000 });
    expect(document.querySelectorAll(".plot-issues > li")).toHaveLength(2);
  }, 15_000);

  it("says plainly when an answer is refused, and when the plot check cannot load", async () => {
    live.on = true;
    const api = mockApi("table");
    api.answerPlotIssue = async () => {
      throw new ApiError("not-allowed");
    };
    render(<RecapPlotCheck api={api} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.leave, 1) }));
    expect(await within(issue(1)).findByText(w.problem["not-allowed"]!)).toBeTruthy();
    cleanup();
    const down = mockApi("table");
    down.plotCheck = async () => {
      throw new ApiError("network");
    };
    render(<RecapPlotCheck api={down} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
    expect(await screen.findByText(w.loadFailed)).toBeTruthy();
  });

  it("lists the approved versions", async () => {
    await open();
    expect(screen.getByText(w.versionLine(1, "You", "2026-10-01"))).toBeTruthy();
  });
});

describe("accessibility", () => {
  it("has nothing serious or critical, with the chat open", async () => {
    await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
});

describe("the real client", () => {
  it("reads the check and posts an answer, a chat message and a decision to the question's own address", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method ?? "GET"} ${new URL(url).pathname} ${init?.body ?? ""}`);
      return new Response(JSON.stringify({ thread: [] }), { status: 200 });
    }) as typeof fetch);
    await api.plotCheck!("5.abc.1700");
    await api.answerPlotIssue!("5.abc.1700", "i1", { kind: "leave" });
    await api.plotChat!("5.abc.1700", "i1", "hi");
    await api.decidePlotProposal!("5.abc.1700", "i1", "p1", "save");
    expect(seen).toEqual([
      "GET /me/recaps/5.abc.1700/plot ",
      'POST /me/recaps/5.abc.1700/plot/i1/answer {"kind":"leave"}',
      'POST /me/recaps/5.abc.1700/plot/i1/chat {"message":"hi"}',
      'POST /me/recaps/5.abc.1700/plot/i1/proposals/p1 {"action":"save"}',
    ]);
  });
});

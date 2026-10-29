/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Plot check on a recap's page (#2124): PlotBot's questions, the four answers, the chat with Save and
// Not that, and the words Approve waits on. It shows only while PLOT_CHECK_LIVE is true (false today).
import { workWords } from "../src/content/work-progress";
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

async function open(text = draft.text, ending = "") {
  live.on = true;
  const api = mockApi("table");
  const blocked = vi.fn();
  const fix = vi.fn();
  const view = render(<RecapPlotCheck api={api} refId={draft.ref + ending} text={text} onBlocked={blocked} onFix={fix} />);
  await screen.findByRole("heading", { name: w.heading });
  return { api, blocked, fix, view };
}
const issue = (n: number) => screen.getAllByRole("group")[n - 1]!;

describe("the plot check", () => {
  it("shows nothing while it is switched off", async () => {
    const off = mockApi("table");
    const { container } = render(<RecapPlotCheck api={off} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
    expect(calls(off, "plotCheck")).toHaveLength(0); // switched off means no request at all
  });

  it("lists each question with the recap's sentence and the earlier note, and Approve waits", async () => {
    const { blocked } = await open();
    expect(within(issue(1)).getByText("Ulfgar opened the gate.")).toBeTruthy();
    expect(within(issue(1)).getByText(/Ulfgar fell to the ghoul/)).toBeTruthy();
    expect(within(issue(1)).getByText(/Earlier, in “The Bridge at Dusk”:/)).toBeTruthy();
    // the server's levels are possible, probable and likely
    expect(within(issue(1)).getByText(new RegExp(w.level.likely!))).toBeTruthy();
    expect(within(issue(2)).getByText(new RegExp(w.level.probable!))).toBeTruthy();
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

  it("Fix the recap sends only the recap's sentence to the fix box, and answers nothing yet", async () => {
    const { api, fix } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.fix, 1) }));
    expect(fix).toHaveBeenCalledWith(w.fixPrefill("Ulfgar opened the gate.", "The Bridge at Dusk"));
    expect(await screen.findByText(w.fixed)).toBeTruthy();
    expect(calls(api, "answerPlotIssue")).toHaveLength(0);
  });

  it("talks it through: the message waits, DMbot answers with a note to keep, and nothing is saved by asking", async () => {
    const { api } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He was raised." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    expect(await within(issue(1)).findByText(w.thinking)).toBeTruthy(); // the bot answers a little later
    expect(await within(issue(1)).findByText(/Ulfgar: alive again from session 7/, undefined, { timeout: 8000 })).toBeTruthy();
    expect(calls(api, "plotChatSend")).toEqual(["plotChatSend:" + draft.ref + ":i1"]);
    expect(calls(api, "decidePlotProposal")).toHaveLength(0);
  }, 15_000);

  it("saves a note only on Save (it reads Saving…, then Saved), and Not that saves nothing", async () => {
    const { api } = await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He was raised." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const notThat = await within(issue(1)).findByRole("button", { name: `${w.notThat}: Ulfgar: alive again from session 7` }, { timeout: 8000 });
    fireEvent.click(notThat);
    expect(await within(issue(1)).findByText(w.proposalState.declined!)).toBeTruthy();
    expect(calls(api, "decidePlotProposal")[0]).toContain(":decline");
    // a second message, saved this time
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He really was." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const saves = await within(issue(1)).findAllByRole("button", { name: new RegExp(`^${w.save}:`) }, { timeout: 8000 });
    fireEvent.click(saves[0]!);
    expect(calls(api, "decidePlotProposal")[1]).toContain(":save");
    expect(await within(issue(1)).findByText(w.proposalState.saving!)).toBeTruthy();
    expect(await within(issue(1)).findByText(w.proposalState.saved!, undefined, { timeout: 8000 })).toBeTruthy();
  }, 30_000);

  it("shows the reason a Save failed, in the bot's words", async () => {
    await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "nosave please" } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const save = await within(issue(1)).findByRole("button", { name: new RegExp(`^${w.save}:`) }, { timeout: 8000 });
    fireEvent.click(save);
    expect(await within(issue(1)).findByText(/changed while it was being saved/, undefined, { timeout: 8000 })).toBeTruthy();
  }, 30_000);

  it("says the hour is full, keeps the message, and sends nothing more (#2774)", async () => {
    const { api } = await open(draft.text, ".hour-full");
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    const box = within(issue(1)).getByLabelText(w.chatLabel) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "He was raised." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const alert = await within(issue(1)).findByText(w.problem["hour-full"]!);
    expect(alert.textContent).toBe("PlotBot has answered a lot this hour. Try again in an hour.");
    expect(box.value).toBe("He was raised."); // the message stays, so it can be sent again
    expect(calls(api, "plotChatSend")).toHaveLength(1);
    expect(screen.queryByText(w.problem["too-soon"]!)).toBeNull(); // not the "talked enough" words
  });

  it("shows a plain sentence from the API as it is, for a message PlotBot never answered (#2774)", async () => {
    await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "silent please" } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    expect(await within(issue(1)).findByText("PlotBot didn't answer. Ask it again.", undefined, { timeout: 8000 })).toBeTruthy();
    expect(within(issue(1)).queryByText(w.chatFailed.failed!)).toBeNull(); // not mapped as a code
  }, 15_000);

  it("shows a Save that timed out with the API's sentence (#2774)", async () => {
    await open(draft.text, ".timed-out");
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "He was raised." } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    const save = await within(issue(1)).findByRole("button", { name: new RegExp(`^${w.save}:`) }, { timeout: 8000 });
    fireEvent.click(save);
    expect(await within(issue(1)).findByText("PlotBot didn't save this in time. Ask it again.", undefined, { timeout: 8000 })).toBeTruthy();
  }, 30_000);

  it("offers no talk when the talk calls are missing", async () => {
    live.on = true;
    const api = mockApi("table");
    delete api.plotChat;
    delete api.plotChatSend;
    render(<RecapPlotCheck api={api} refId={draft.ref} text={draft.text} onBlocked={() => {}} onFix={() => {}} />);
    await screen.findByRole("heading", { name: w.heading });
    expect(screen.queryByRole("button", { name: w.answerFor(w.answers.talk, 1) })).toBeNull();
  });

  it("says DMbot could not answer a message, so it can be sent again", async () => {
    await open();
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
    fireEvent.input(within(issue(1)).getByLabelText(w.chatLabel), { target: { value: "please fail" } });
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.chatSend }));
    expect(await within(issue(1)).findByText(w.chatFailed.no_answer!, undefined, { timeout: 8000 })).toBeTruthy();
  }, 15_000);

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
    expect(screen.getByText(/^Version 1, approved on /)).toBeTruthy();
    expect(document.body.textContent).not.toContain("100000000000000001"); // never an account number
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
      return new Response(JSON.stringify({ messages: [] }), { status: 200 });
    }) as typeof fetch);
    await api.plotCheck!("5.abc.1700");
    await api.answerPlotIssue!("5.abc.1700", "i1", { kind: "leave" });
    await api.plotChatSend!("5.abc.1700", "i1", "hi");
    await api.plotChat!("5.abc.1700", "i1");
    await api.decidePlotProposal!("5.abc.1700", "i1", "p1", "save");
    expect(seen).toEqual([
      "GET /me/recaps/5.abc.1700/plot ",
      'POST /me/recaps/5.abc.1700/plot/i1/answer {"kind":"leave"}',
      'POST /me/recaps/5.abc.1700/plot/i1/chat {"text":"hi"}',
      "GET /me/recaps/5.abc.1700/plot/i1/chat ",
      'POST /me/recaps/5.abc.1700/plot/i1/proposals/p1 {"action":"save"}',
    ]);
  });

  it.each([
    [422, "bad_answer", "bad-answer"],
    [409, "stale", "stale"],
    [409, "plot_open", "plot-open"],
    [429, "too_soon", "too-soon"],
    [429, "hour_full", "hour-full"],
    [409, "not_allowed", "not-allowed"],
  ])("reads %s %s as %s", async (status, code, kind) => {
    const api = httpApi("https://api.example", (async () => new Response(JSON.stringify({ detail: code }), { status })) as typeof fetch);
    await expect(api.answerPlotIssue!("5.abc.1700", "i1", { kind: "leave" })).rejects.toMatchObject({ kind });
  });
});

// Last in the file on purpose: these use fake timers, and axe (above) must run on real ones.
describe("Approve never stays locked", () => {
  const never = { state: "checking" as const, approveBlocked: true, version: 1, textHash: "h", issues: [], versions: [] };

  async function watch(look: () => Promise<typeof never>, afterOpen: () => void = () => {}) {
    live.on = true;
    vi.useFakeTimers();
    const api = mockApi("table");
    api.plotCheck = look;
    const blocked = vi.fn();
    render(<RecapPlotCheck api={api} refId={draft.ref} text="x" onBlocked={blocked} onFix={() => {}} />);
    await vi.advanceTimersByTimeAsync(200); // (the page runs its first look after a short frame wait)
    expect(blocked).toHaveBeenLastCalledWith(w.waitingCheck); // while it runs, Approve waits
    afterOpen();
    await vi.advanceTimersByTimeAsync(70_000); // past the limit
    return blocked;
  }

  it("opens Approve with a plain line when the check never finishes", async () => {
    const blocked = await watch(async () => never);
    expect(blocked).toHaveBeenLastCalledWith(null);
    expect(screen.getByText(w.gaveUp)).toBeTruthy();
    expect(screen.getByText(workWords.stoppedLooking)).toBeTruthy(); // the clock stopped with the looking
    expect(screen.queryByText(/Running for/)).toBeNull();
  });

  it("keeps looking after a look fails, and still opens Approve at the limit", async () => {
    let looks = 0;
    let failing = false;
    const blocked = await watch(
      async () => {
        looks += 1;
        if (failing) throw new ApiError("network"); // the opening look works, every later one fails
        return never;
      },
      () => {
        failing = true;
      },
    );
    expect(looks).toBeGreaterThan(5); // it did not stop at the first failure
    expect(blocked).toHaveBeenLastCalledWith(null);
    expect(screen.getByText(w.gaveUp)).toBeTruthy();
  });
});

describe("what the real API can say", () => {
  it("never holds Approve when the check is unavailable, and says so", async () => {
    live.on = true;
    const api = mockApi("table");
    api.plotCheck = async () => ({ state: "unavailable", approveBlocked: false, version: 1, textHash: "h", issues: [], versions: [] });
    const blocked = vi.fn();
    render(<RecapPlotCheck api={api} refId={draft.ref} text="x" onBlocked={blocked} onFix={() => {}} />);
    expect(await screen.findByText(w.unavailable)).toBeTruthy();
    expect(blocked).not.toHaveBeenCalledWith(expect.stringContaining("Plot check"));
  });

  it("reads the check again when an answer finds the recap changed (stale)", async () => {
    live.on = true;
    const api = mockApi("table");
    api.answerPlotIssue = async () => {
      throw new ApiError("stale");
    };
    render(<RecapPlotCheck api={api} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
    await screen.findByRole("heading", { name: w.heading });
    const before = calls(api, "plotCheck").length;
    fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.leave, 1) }));
    expect(await within(issue(1)).findByText(w.problem.stale!)).toBeTruthy();
    await waitFor(() => expect(calls(api, "plotCheck").length).toBeGreaterThan(before));
  });
  describe("a message PlotBot never answers", () => {
    async function stuckTalk(afterMs: number) {
      live.on = true;
      vi.useFakeTimers();
      const api = mockApi("table");
      api.plotChat = async () => [{ id: "m0", role: "dm", text: "hi", state: "waiting", error: "", proposals: [], at: 1790000000 }];
      render(<RecapPlotCheck api={api} refId={draft.ref} text="x" onBlocked={() => {}} onFix={() => {}} />);
      await vi.advanceTimersByTimeAsync(200);
      fireEvent.click(within(issue(1)).getByRole("button", { name: w.answerFor(w.answers.talk, 1) }));
      await vi.advanceTimersByTimeAsync(afterMs);
    }

    it("says its own words after a quiet minute, and the clock still runs while the page is still looking", async () => {
      await stuckTalk(70_000);
      expect(screen.getByText(w.chatQuiet)).toBeTruthy(); // not the generic "try again", which has no button here
      expect(screen.getByText(/Running for/)).toBeTruthy();
    });

    it("stops the clock and says to come back when the page gives up", async () => {
      await stuckTalk(300_000);
      expect(screen.getByText(w.chatSlow)).toBeTruthy();
      expect(screen.getByText(workWords.stoppedLooking)).toBeTruthy();
      expect(screen.queryByText(/Running for/)).toBeNull();
    });
  });
});

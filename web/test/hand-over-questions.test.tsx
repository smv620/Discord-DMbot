/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Does Claude still have it?" on the Questions page (#4122, the web half of #4115): the card in Discord's words, No (not any more) with
// its Undo, Yes both, Not now, "already answered" and every failure said plainly, DM only, and the real client against core's
// routes. Run against the pretend API (a campaign ref ending `.handover` has two questions).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import HandOverQuestions from "../src/account/HandOverQuestions";
import { mockApi } from "../src/account/mock";
import { cardOf, handOverWords as w, the } from "../src/content/hand-over-words";

afterEach(cleanup);

let n = 0;
const open = (ending = ".handover") => {
  const api = mockApi("table");
  const ref = `100.cmp${(n += 1)}${ending}`;
  render(<HandOverQuestions api={api} campaignRef={ref} id="q" />);
  return { api, ref };
};
/** The Undo button once the answer before it has been saved (it is off while the page is saving). */
const undoReady = async (): Promise<HTMLElement> => {
  const button = await screen.findByRole("button", { name: w.undo });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  return button;
};
const FIRST = { itemName: "Frost Brand", olderName: "Claude", newerName: "Belleros" };

describe("the words", () => {
  it("add 'the' only when the name has none", () => {
    expect(the("Frost Brand")).toBe("the Frost Brand");
    expect(the("the Lantern of Ash")).toBe("the Lantern of Ash");
    expect(the("a Sword")).toBe("a Sword");
    expect(the("Anvil")).toBe("the Anvil");
  });
});

describe("the question", () => {
  it("is asked in Discord's words, with what each answer does", async () => {
    open();
    expect(await screen.findByText("You said Belleros has the Frost Brand now. It's one of a kind.")).toBeTruthy();
    expect(screen.getByText("Does Claude still have it?")).toBeTruthy();
    expect(screen.getByText(/Claude's part becomes history\./)).toBeTruthy();
    expect(screen.getByRole("heading", { name: `${w.heading} (${w.waiting(2)})` })).toBeTruthy();
    expect(cardOf({ ...FIRST } as never).ask).toBe("Does Claude still have it?");
  });

  it("a No says what was done, offers Undo, and the next question comes up", async () => {
    const { api, ref } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    expect(await screen.findByText(w.done("Claude", "Belleros", "Frost Brand"))).toBeTruthy();
    expect(await screen.findByText("You said Okra has the Lantern of Ash now. It's one of a kind.")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith(`answerHandOver:${ref}:`) && c.endsWith(":no"))).toBe(true);
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("Undo takes it back: the question is back and the line says so", async () => {
    const { api } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    fireEvent.click(await undoReady());
    expect(await screen.findByText(w.undone("Claude", "Frost Brand"))).toBeTruthy();
    expect(await screen.findByText("Does Claude still have it?")).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
    expect(api.calls.filter((c) => c.includes(":undo:"))).toHaveLength(1);
  });

  it("Yes, both says it won't ask again, and offers no Undo", async () => {
    const { api } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.both }));
    expect(await screen.findByText(w.bothKept("Claude", "Belleros", "Frost Brand"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
    expect(api.calls.some((c) => c.endsWith(":both"))).toBe(true);
  });

  it("Not now sends nothing and moves to the next", async () => {
    const { api } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.notNow }));
    expect(await screen.findByText("You said Okra has the Lantern of Ash now. It's one of a kind.")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("answerHandOver"))).toBe(false);
  });

  it("shows nothing when nothing waits", async () => {
    const { container, api, ref } = (() => {
      const api = mockApi("table");
      const ref = `100.cmp${(n += 1)}`;
      return { ...render(<HandOverQuestions api={api} campaignRef={ref} id="q" />), api, ref };
    })();
    await waitFor(() => expect(api.calls).toContain(`handOverQuestions:${ref}`));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("when an answer cannot be saved", () => {
  const say = async (ending: string, button: string, expected: string) => {
    open(ending);
    fireEvent.click(await screen.findByRole("button", { name: button }));
    expect(await screen.findByText(expected)).toBeTruthy();
  };
  it("says it was already answered and shows what is left", () => say(".handover-gone", w.no, w.gone));
  it("says a failed save plainly", () => say(".handover-fail", w.both, w.couldNot));
  it("says only a DM can answer", () => say(".handover-notallowed", w.no, w.notAllowed));
  it("says an Undo that is too late, or in the way", async () => {
    for (const [ending, words] of [[".handover-late", w.tooLate], [".handover-changed", w.changed]] as const) {
      open(ending);
      fireEvent.click(await screen.findByRole("button", { name: w.no }));
      fireEvent.click(await undoReady());
      expect(await screen.findByText(words)).toBeTruthy();
      cleanup();
    }
  });
});

describe("DM only", () => {
  it("a player is shown nothing at all, not an error", async () => {
    const api = mockApi("table");
    const ref = `100.cmp${(n += 1)}.handover.web-player`;
    const { container } = render(<HandOverQuestions api={api} campaignRef={ref} id="q" />);
    await waitFor(() => expect(api.calls).toContain(`handOverQuestions:${ref}`));
    await waitFor(() => expect(container.textContent).toBe(""));
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("a server that cannot be reached still says so", async () => {
    const api = mockApi("table");
    api.handOverQuestions = async () => {
      throw new ApiError("server");
    };
    render(<HandOverQuestions api={api} campaignRef="100.cmpS.handover" id="q" />);
    expect(await screen.findByText(w.failed)).toBeTruthy();
  });
});

describe("pressing", () => {
  it("a second press while one is being saved sends only one answer", async () => {
    const api = mockApi("table");
    let finish: (v: { answerId: string; undoable: boolean }) => void = () => {};
    api.answerHandOver = () => new Promise((r) => (finish = r));
    render(<HandOverQuestions api={api} campaignRef="100.cmpD.handover" id="q" />);
    const button = await screen.findByRole("button", { name: w.no });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("button", { name: w.both }));
    finish({ answerId: "x", undoable: true });
    await waitFor(() => expect(screen.queryByText(w.saving)).toBeNull());
    expect(api.calls.filter((c) => c.startsWith("handOverQuestions")).length).toBeGreaterThan(0);
  });

  it("an answered question does not stay up when the re-read fails", async () => {
    const api = mockApi("table");
    let reads = 0;
    const read = api.handOverQuestions!;
    api.handOverQuestions = async (ref) => {
      if ((reads += 1) > 1) throw new ApiError("server");
      return read(ref);
    };
    api.answerHandOver = async () => ({ answerId: "x", undoable: false });
    render(<HandOverQuestions api={api} campaignRef="100.cmpR.handover" id="q" />);
    fireEvent.click(await screen.findByRole("button", { name: w.both }));
    expect(await screen.findByText("You said Okra has the Lantern of Ash now. It's one of a kind.")).toBeTruthy();
    expect(screen.queryByText("You said Belleros has the Frost Brand now. It's one of a kind.")).toBeNull();
  });

  it("a first answer that fails with too-late or changed does not talk about undoing", async () => {
    const api = mockApi("table");
    api.answerHandOver = async () => {
      const failure = new ApiError("server");
      failure.note = "too-late";
      throw failure;
    };
    render(<HandOverQuestions api={api} campaignRef="100.cmpL.handover" id="q" />);
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    expect(await screen.findByText(w.couldNot)).toBeTruthy();
    expect(screen.queryByText(w.tooLate)).toBeNull();
  });
});

/** A pretend network: each request gets the next answer, so the page sends one answer and reads it back. */
function net(answers: { status?: number; body: unknown }[]) {
  const queue = [...answers];
  const seen: string[] = [];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push(`${init?.method ?? "GET"} ${String(url).replace("https://api.example", "")} ${typeof init?.body === "string" ? init.body : ""}`.trim());
    const next = queue.shift() ?? { body: {} };
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
const TIES = { olderTie: "a".repeat(32), newerTie: "b".repeat(32) };
const ID = "c".repeat(32);

describe("the real client against core's routes", () => {
  it("reads the list, queues an answer, polls it and reports what can be undone", async () => {
    await import("../src/account/api-names");
    const { api, seen } = net([
      { body: { questions: [{ ...TIES, item: "i", itemName: "Frost Brand", older: "o", olderName: "Claude", newer: "n", newerName: "Belleros", predicate: "owns", madeAt: 1 }] } },
      { status: 202, body: { id: ID } },
      { body: { state: "working", progress: null } },
      { body: { state: "done", undoable: true, progress: null } },
    ]);
    const list = await api.handOverQuestions!("1.cmp1");
    expect(list).toHaveLength(1);
    expect(seen[0]).toBe("GET /me/campaigns/1.cmp1/hand-over-questions");
    const got = await api.answerHandOver!("1.cmp1", TIES, "no");
    expect(got).toEqual({ answerId: ID, undoable: true });
    expect(seen[1]).toBe(`POST /me/campaigns/1.cmp1/hand-over-questions/answer {"olderTie":"${TIES.olderTie}","newerTie":"${TIES.newerTie}","answer":"no"}`);
    expect(seen[2]).toBe(`GET /me/campaigns/1.cmp1/hand-over-questions/answer/${ID}`);
  });

  it("an answer that never finishes ends as still waiting, and five failed polls end as a server error", async () => {
    await import("../src/account/api-names");
    vi.useFakeTimers();
    try {
      const poll = Array.from({ length: 200 }, () => ({ body: { state: "working" } }));
      const { api: slow } = net([{ status: 202, body: { id: ID } }, ...poll]);
      const first = slow.answerHandOver!("1.cmp1", TIES, "both").catch((e) => e as ApiError);
      await vi.runAllTimersAsync();
      expect(((await first) as ApiError).kind).toBe("still-waiting");
      const broken = Array.from({ length: 6 }, () => ({ status: 500, body: {} }));
      const { api: down } = net([{ status: 202, body: { id: ID } }, ...broken]);
      const second = down.answerHandOver!("1.cmp1", TIES, "both").catch((e) => e as ApiError);
      await vi.runAllTimersAsync();
      expect(((await second) as ApiError).kind).toBe("server");
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends the answer to take back with an undo, and keeps the bot's word for a failure", async () => {
    await import("../src/account/api-names");
    const { api, seen } = net([{ status: 202, body: { id: ID } }, { body: { state: "failed", error: "too-late" } }]);
    let kind = "";
    let note = "";
    try {
      await api.answerHandOver!("1.cmp1", TIES, "undo", "d".repeat(32));
    } catch (e) {
      kind = (e as ApiError).kind;
      note = (e as ApiError).note ?? "";
    }
    expect(seen[0]).toContain(`"answer":"undo","undoes":"${"d".repeat(32)}"`);
    expect([kind, note]).toEqual(["server", "too-late"]);
  });
});

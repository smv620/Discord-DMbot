/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Things the characters learned" on the Questions page (#3863 step 2), behind REVEALS_LIVE: each proposal is kept or turned down, and a keep
// can be undone. A copy of the leave card's pattern, one new kind. Only the answered row leaves the list.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type QuestionCounts, type RevealProposal } from "../src/account/api";
import Questions from "../src/account/Questions";
import RevealQuestions from "../src/account/RevealQuestions";
import { mockApi } from "../src/account/mock";
import { kindWords, questionsPage } from "../src/content/questions-words";
import { revealWords as w } from "../src/content/reveal-questions-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/reveals-switch", () => ({
  get REVEALS_LIVE() {
    return live.on;
  },
}));
// The Questions page itself is on here; the kind's own switch is the one under test.
vi.mock("../src/content/questions-switch", () => ({ QUESTIONS_LIVE: true }));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
const ITEMS: RevealProposal[] = [
  { id: "rv-player", who: "Tavin", what: "the ferryman owes the mill a favour", how: "overheard at the ford", player: true, guessing: false },
  { id: "rv-guess", who: "Mira", what: "the cellar door is trapped", how: "guessed from a sound", player: false, guessing: true },
];

/** A server that keeps the proposals: a keep or a not takes its proposal out; the Undo (answerJoin) puts it back. */
function server(start: RevealProposal[] = ITEMS) {
  const api = mockApi("table");
  let state = structuredClone(start);
  const sent: { id: string; answer: string }[] = [];
  const undone: number[] = [];
  const kept = new Map<number, RevealProposal>();
  let batch = 80;
  api.revealQuestions = async () => structuredClone(state);
  api.answerReveal = async (_ref, change) => {
    sent.push(change);
    const item = state.find((p) => p.id === change.id);
    if (!item) throw new ApiError("gone");
    state = state.filter((p) => p.id !== change.id);
    if (change.answer !== "keep") return { batch: null };
    batch += 1;
    kept.set(batch, item);
    return { batch };
  };
  api.answerJoin = async (_ref, change) => {
    if (change.action !== "undo") throw new Error("only the Undo is used here");
    undone.push(change.batch);
    const back = kept.get(change.batch);
    if (!back) throw new ApiError("undo-late");
    state = [...state, back];
    return { batch: null, given: null };
  };
  return { api, sent, undone };
}

async function open(srv = server()) {
  render(<RevealQuestions api={srv.api} campaignRef={REF} id="reveal-test" />);
  await screen.findByRole("heading", { name: w.title });
  return srv;
}
const row = (who: string) => (screen.getByText(w.learned(who, ITEMS.find((p) => p.who === who)!.what, ITEMS.find((p) => p.who === who)!.how))).closest("div") as HTMLElement;

describe("Things the characters learned on the Questions page (#3863 step 2)", () => {
  it("a DM sees each proposal in the card's words, with the player and guess lines when they apply", async () => {
    await open();
    expect(screen.getByText(w.intro)).toBeTruthy();
    expect(screen.getByText(w.learned("Tavin", "the ferryman owes the mill a favour", "overheard at the ford"))).toBeTruthy();
    expect(screen.getByText(w.player("Tavin"))).toBeTruthy();
    expect(screen.queryByText(w.guessing("Tavin"))).toBeNull();
    expect(screen.getByText(w.guessing("Mira"))).toBeTruthy();
    expect(screen.queryByText(w.player("Mira"))).toBeNull();
    expect(screen.getAllByRole("button", { name: w.keep })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: w.notThis })).toHaveLength(2);
  });

  it("Keep says kept and offers Undo; Not this says okay and removes the row", async () => {
    const srv = await open();
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    expect(await screen.findByText(w.kept)).toBeTruthy();
    expect(srv.sent).toEqual([{ id: "rv-player", answer: "keep" }]);
    expect(await screen.findByRole("button", { name: w.undo })).toBeTruthy();
    expect(screen.queryByText(w.learned("Tavin", "the ferryman owes the mill a favour", "overheard at the ford"))).toBeNull();
    fireEvent.click(within(row("Mira")).getByRole("button", { name: w.notThis }));
    expect(await screen.findByText(w.okay)).toBeTruthy();
    expect(srv.sent[1]).toEqual({ id: "rv-guess", answer: "not" });
    expect(screen.queryByText(w.learned("Mira", "the cellar door is trapped", "guessed from a sound"))).toBeNull();
  });

  it("a keep that changed nothing says they already knew it and offers no Undo", async () => {
    const api = mockApi("table");
    const known = [{ id: "rv-known", who: "Brynn", what: "the heir is alive", how: "a rumour in the tavern", player: false, guessing: false }];
    api.revealQuestions = async () => known.slice();
    api.answerReveal = async () => ({ batch: null });
    render(<RevealQuestions api={api} campaignRef={REF} id="reveal-test" />);
    fireEvent.click(await screen.findByRole("button", { name: w.keep }));
    expect(await screen.findByText(w.alreadyKnown)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("Undo calls the join undo with the batch and says undone; a new answer takes the old Undo away", async () => {
    const srv = await open();
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    const undo = await screen.findByRole("button", { name: w.undo });
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(srv.undone).toEqual([81]);
    expect(await screen.findByText(w.learned("Tavin", "the ferryman owes the mill a favour", "overheard at the ford"))).toBeTruthy();
    // A keep, then a not on another row: the Undo of the keep is gone.
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    const again = await screen.findByRole("button", { name: w.undo });
    await waitFor(() => expect((again as HTMLButtonElement).disabled).toBe(false));
    const mira = within(row("Mira")).getByRole("button", { name: w.notThis });
    await waitFor(() => expect((mira as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(mira);
    await screen.findByText(w.okay);
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("a gone proposal says so and reads the list again", async () => {
    const srv = await open();
    const reads = vi.spyOn(srv.api, "revealQuestions");
    srv.api.answerReveal = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.gone);
    await waitFor(() => expect(reads).toHaveBeenCalled());
  });

  it("a failed press says nothing changed; a failed load offers Try again", async () => {
    const srv = await open();
    srv.api.answerReveal = async () => {
      throw new ApiError("network");
    };
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNot);

    cleanup();
    const bad = mockApi("table");
    bad.revealQuestions = async () => {
      throw new ApiError("network");
    };
    render(<RevealQuestions api={bad} campaignRef={REF} id="reveal-bad" />);
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("an empty list says nothing is waiting", async () => {
    await open(server([]));
    expect(await screen.findByText(w.empty)).toBeTruthy();
  });

  it("the page draws nothing for a player", async () => {
    const api = mockApi("table");
    api.revealQuestions = async () => {
      throw new ApiError("not-allowed");
    };
    const { container } = render(<RevealQuestions api={api} campaignRef="100.cmp1.web-player" id="reveal-player" />);
    await waitFor(() => expect((container.textContent ?? "").length).toBeGreaterThanOrEqual(0));
    expect(screen.queryByRole("button", { name: w.keep })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("every button waits while a press is with the bot", async () => {
    const srv = await open();
    let release!: () => void;
    srv.api.answerReveal = () =>
      new Promise<{ batch: number | null }>((resolve) => {
        release = () => resolve({ batch: 90 });
      });
    fireEvent.click(within(row("Tavin")).getByRole("button", { name: w.keep }));
    await waitFor(() => expect((within(row("Tavin")).getByRole("button", { name: w.keep }) as HTMLButtonElement).disabled).toBe(true));
    expect((within(row("Mira")).getByRole("button", { name: w.keep }) as HTMLButtonElement).disabled).toBe(true);
    release();
    expect(await screen.findByText(w.kept)).toBeTruthy();
  });

  it("the Questions page answers the kind here only while its switch is on", async () => {
    const counts: QuestionCounts = {
      serverNow: Math.floor(Date.now() / 1000),
      campaigns: [
        {
          ref: REF,
          name: "The Ashen Road",
          total: 2,
          countedAt: Math.floor(Date.now() / 1000) - 30,
          kinds: [{ kind: "reveals", count: 2, oldest: Math.floor(Date.now() / 1000) - 100 }],
        },
      ],
    };
    const label = `${kindWords("reveals").label} · 2`;
    const on = mockApi("table");
    on.myQuestions = async () => counts;
    render(<Questions api={on} />);
    const line = (await screen.findByText(label)).closest("li") as HTMLElement;
    expect(within(line).getByRole("button", { name: `${questionsPage.showHere}: ${kindWords("reveals").label}` })).toBeTruthy();
    cleanup();

    live.on = false;
    const off = mockApi("table");
    off.myQuestions = async () => counts;
    render(<Questions api={off} />);
    const offLine = (await screen.findByText(label)).closest("li") as HTMLElement;
    expect(within(offLine).queryByRole("button", { name: /Show here/ })).toBeNull();
    expect(within(offLine).getByText(questionsPage.discord)).toBeTruthy();
  });
});

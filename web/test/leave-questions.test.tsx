/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Still in the party?" question on the Questions page (#4895, party knowledge 6a), behind PARTY_LEAVES_LIVE: a death or a goodbye
// answered on the page as in Discord's card. Each answer removes only that character's row, and Undo asks the question again.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type LeaveGroup, type QuestionCounts } from "../src/account/api";
import LeaveQuestions from "../src/account/LeaveQuestions";
import { mockApi } from "../src/account/mock";
import Questions from "../src/account/Questions";
import { leaveWords as w, shortName } from "../src/content/leave-questions-words";
import { kindWords, questionsPage } from "../src/content/questions-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/party-leaves-switch", () => ({
  get PARTY_LEAVES_LIVE() {
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
const laterName = new RegExp(`^${w.later}`);
const GROUPS: LeaveGroup[] = [
  { group: "dead-1", kind: "died", characters: [{ id: "lv-okthandom", name: "Okthandom" }] },
  {
    group: "bye-1",
    kind: "leave",
    characters: [
      { id: "lv-tavin", name: "Tavin" },
      { id: "lv-brynn", name: "Brynn Ashwood-of-the-Northern-Wastes" },
    ],
  },
];

/** A server that keeps the questions: a "left" answer takes its character out, and the Undo puts it back. */
function server(start: LeaveGroup[] = GROUPS) {
  const api = mockApi("table");
  let state = structuredClone(start);
  const sent: { character: string; answer: string }[] = [];
  const undone: number[] = [];
  const left = new Map<number, { group: string; kind: LeaveGroup["kind"]; character: { id: string; name: string } }>();
  let batch = 100;
  api.leaveQuestions = async () => structuredClone(state);
  api.answerLeave = async (_ref, change) => {
    sent.push(change);
    const group = state.find((g) => g.characters.some((c) => c.id === change.character));
    if (!group) throw new ApiError("gone");
    const character = group.characters.find((c) => c.id === change.character)!;
    state = state
      .map((g) => (g.group === group.group ? { ...g, characters: g.characters.filter((c) => c.id !== change.character) } : g))
      .filter((g) => g.characters.length > 0);
    if (change.answer !== "left") return { batch: null };
    batch += 1;
    left.set(batch, { group: group.group, kind: group.kind, character });
    return { batch };
  };
  api.answerJoin = async (_ref, change) => {
    if (change.action !== "undo") throw new Error("only the Undo is used here");
    undone.push(change.batch);
    const back = left.get(change.batch);
    if (!back) throw new ApiError("undo-late");
    const there = state.some((g) => g.group === back.group);
    state = there
      ? state.map((g) => (g.group === back.group ? { ...g, characters: [...g.characters, back.character] } : g))
      : [...state, { group: back.group, kind: back.kind, characters: [back.character] }];
    return { batch: null, given: null };
  };
  return { api, sent, undone };
}

async function open(srv = server()) {
  render(<LeaveQuestions api={srv.api} campaignRef={REF} id="leave-test" />);
  await screen.findByRole("heading", { name: w.title });
  return srv;
}

describe("Still in the party? on the Questions page (#4895)", () => {
  it("a death and a goodbye each show their question, with the two buttons and one Later per group", async () => {
    await open();
    expect(screen.getByText(w.died("Okthandom"))).toBeTruthy();
    expect(screen.getByText(w.leave("Tavin"))).toBeTruthy();
    // One character in a group reads the plain words; several read with their names.
    expect(screen.getByRole("button", { name: w.left })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.stays })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tavin: left" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tavin: stays" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: laterName })).toHaveLength(2);
  });

  it("a name longer than sixteen characters is cut in its buttons, with an ellipsis", async () => {
    await open();
    // Core's rule (party_leave.py): a longer name is cut to fifteen characters and "…", sixteen in all.
    expect(shortName("Brynn Ashwood-of-the-Northern-Wastes")).toBe("Brynn Ashwood-o…");
    expect(shortName("Okthandom")).toBe("Okthandom");
    expect(screen.getByRole("button", { name: "Brynn Ashwood-o…: left" })).toBeTruthy();
  });

  it("a name of exactly sixteen characters is not cut; seventeen is cut to fifteen and an ellipsis", () => {
    const sixteen = "s".repeat(16);
    const seventeen = "t".repeat(17);
    expect(shortName(sixteen)).toBe(sixteen);
    expect(shortName(seventeen)).toBe(`${"t".repeat(15)}…`);
    expect([...shortName(seventeen)]).toHaveLength(16);
  });

  it("Later hides its group, sends nothing, and says it is kept for later", async () => {
    const srv = await open();
    fireEvent.click(screen.getAllByRole("button", { name: laterName })[1]!);
    expect(await screen.findByText(w.laterDone)).toBeTruthy();
    expect(screen.queryByText(w.leave("Tavin"))).toBeNull();
    expect(screen.getByText(w.died("Okthandom"))).toBeTruthy();
    expect(srv.sent).toEqual([]);
  });

  it("a group set aside with Later stays hidden after another group is answered", async () => {
    const srv = await open();
    // Later on the death (the first group), then a goodbye in the second group is answered.
    fireEvent.click(screen.getAllByRole("button", { name: laterName })[0]!);
    expect(await screen.findByText(w.laterDone)).toBeTruthy();
    expect(screen.queryByText(w.died("Okthandom"))).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tavin: stays" }));
    expect(await screen.findByText(w.stayedDone("Tavin"))).toBeTruthy();
    expect(srv.sent).toEqual([{ character: "lv-tavin", answer: "stays" }]);
    expect(screen.queryByText(w.died("Okthandom"))).toBeNull();
  });

  it("Left the party sends the answer, says so, and offers Undo, which asks the question again", async () => {
    const srv = await open();
    fireEvent.click(screen.getByRole("button", { name: w.left }));
    expect(await screen.findByText(w.leftDone("Okthandom"))).toBeTruthy();
    expect(srv.sent).toEqual([{ character: "lv-okthandom", answer: "left" }]);
    expect(screen.queryByText(w.died("Okthandom"))).toBeNull();
    const undo = await screen.findByRole("button", { name: w.undo });
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    expect(await screen.findByText(w.undone("Okthandom"))).toBeTruthy();
    expect(srv.undone).toEqual([101]);
    expect(await screen.findByText(w.died("Okthandom"))).toBeTruthy();
  });

  it("Still in the party sends stays and removes only that character's row", async () => {
    const srv = await open();
    fireEvent.click(screen.getByRole("button", { name: "Tavin: stays" }));
    expect(await screen.findByText(w.stayedDone("Tavin"))).toBeTruthy();
    expect(srv.sent).toEqual([{ character: "lv-tavin", answer: "stays" }]);
    expect(screen.queryByText(w.leave("Tavin"))).toBeNull();
    // Brynn is now alone in her group, so her buttons read the plain words; her row is still there.
    expect(screen.getByText(w.leave("Brynn Ashwood-of-the-Northern-Wastes"))).toBeTruthy();
    // Okthandom and Brynn are each alone in their group now: two plain "Left the party" buttons.
    expect(screen.getAllByRole("button", { name: w.left })).toHaveLength(2);
  });

  it("an answer already given by another DM says so and changes nothing", async () => {
    const srv = await open();
    srv.api.answerLeave = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(screen.getByRole("button", { name: w.left }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe(w.gone);
    expect(screen.getByText(w.died("Okthandom"))).toBeTruthy();
  });

  it("an Undo that is too late says so, and names the character", async () => {
    const srv = await open();
    fireEvent.click(screen.getByRole("button", { name: w.left }));
    const undo = await screen.findByRole("button", { name: w.undo });
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    srv.api.answerJoin = async () => {
      throw new ApiError("undo-late");
    };
    fireEvent.click(undo);
    expect((await screen.findByRole("alert")).textContent).toBe(w.tooLate("Okthandom"));
  });

  it("a Left that ran out of wait says it is not sure, and never that nothing changed; the row is gone because the bot took it", async () => {
    const srv = await open();
    const base = srv.api.answerLeave!;
    srv.api.answerLeave = async (ref, change) => {
      await base(ref, change); // the bot applied it ...
      throw new ApiError("still-waiting"); // ... but the wait ran out before the page heard
    };
    fireEvent.click(screen.getByRole("button", { name: w.left }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(w.unsure);
    expect(alert.textContent).not.toContain("Nothing changed");
    expect(screen.queryByText(w.died("Okthandom"))).toBeNull();
  });

  it("a failed read after an answer keeps the rows and says the list may be out of date", async () => {
    const srv = await open();
    // The list read after the answer fails; the rows already on screen stay.
    srv.api.leaveQuestions = async () => {
      throw new ApiError("network");
    };
    fireEvent.click(screen.getByRole("button", { name: "Tavin: stays" }));
    expect((await screen.findByRole("alert")).textContent).toContain(w.refreshFailed);
  });

  it("any other failure says nothing changed", async () => {
    const srv = await open();
    srv.api.answerLeave = async () => {
      throw new ApiError("network");
    };
    fireEvent.click(screen.getByRole("button", { name: w.left }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNot);
  });

  it("draws nothing while PARTY_LEAVES_LIVE is off, and makes no read", async () => {
    live.on = false;
    const api = mockApi("table");
    let asked = false;
    api.leaveQuestions = async () => {
      asked = true;
      return GROUPS;
    };
    const { container } = render(<LeaveQuestions api={api} campaignRef={REF} id="leave-test" />);
    expect(container.textContent).toBe("");
    expect(asked).toBe(false);
  });

  it("the Questions page answers the kind here once its switch is on, and says Discord while it is off", async () => {
    const counts: QuestionCounts = {
      serverNow: Math.floor(Date.now() / 1000),
      campaigns: [
        {
          ref: REF,
          name: "The Ashen Road",
          total: 2,
          countedAt: Math.floor(Date.now() / 1000) - 30,
          kinds: [{ kind: "party_leaves", count: 2, oldest: Math.floor(Date.now() / 1000) - 100 }],
        },
      ],
    };
    const label = `${kindWords("party_leaves").label} · 2`;
    const on = mockApi("table");
    on.myQuestions = async () => counts;
    render(<Questions api={on} />);
    const line = (await screen.findByText(label)).closest("li") as HTMLElement;
    fireEvent.click(within(line).getByRole("button", { name: `${questionsPage.showHere}: ${kindWords("party_leaves").label}` }));
    // Opening it draws the card itself, with the question from the page's own list.
    expect(await screen.findByText(w.died("Okthandom"))).toBeTruthy();
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

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Let me choose" (#4891, the review of #4892): the open page outlives the question leaving the join list, each group's page keeps
// its own picks, focus moves with the page, and the timed and the rarer lines. Its own file, beside join-choose.test.tsx.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type JoinChoices } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import JoinChoose from "../src/account/JoinChoose";
import JoinQuestions from "../src/account/JoinQuestions";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const REF = "100.cmp1";
const GROUP = "g1";
const mira: JoinChoices = {
  madeAt: 1_800_000_000,
  choices: {
    members: [{ id: "n1234567", name: "Mira" }],
    sessions: [{ at: 1000, number: 3, counts: { n1234567: 14 } }],
    threads: [{ id: "t1", name: "The Ashen Road", counts: { n1234567: 9 } }],
    sessionsInAll: 1,
    threadsInAll: 1,
    nobody: false,
  },
};

/** The API for these tests: the choices as given, a pick that gives Mira 14, and an Undo that is recorded. */
function choiceApi(over: { choices?: JoinChoices; ask?: () => Promise<void> } = {}) {
  const undos: unknown[] = [];
  const api = mockApi("table");
  api.askJoinChoices = async () => {
    if (over.ask) await over.ask();
  };
  api.joinChoices = async () => over.choices ?? mira;
  api.joinChoose = async () => ({ chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: false, batch: 9 });
  api.answerJoin = async (_ref, change) => {
    undos.push(change);
    return { batch: null, given: null };
  };
  return { api, undos };
}

const PICK_LINE = "✅ Mira now knows 14 things the rest of the party knew from session 3. Nothing was sent to Mira's player.";

describe("the open page outlives the question leaving the list (#4891)", () => {
  it("after the question drops out, the card keeps its picks and Undos; Close reads the list and closes the card; another group's page starts with no picks", async () => {
    let groupsNow: { group: string; answer: "waiting" | "everyone"; names: string[] }[] = [{ group: GROUP, answer: "waiting", names: ["Mira"] }];
    let reads = 0;
    const { api } = choiceApi();
    api.joinQuestions = async () => {
      reads += 1;
      return structuredClone(groupsNow);
    };
    render(<JoinQuestions api={api} campaignRef={REF} id="jq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Let me choose" }));
    await screen.findByText("Choose what Mira knows. Nothing changes until you pick.");
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    expect(await screen.findByText(PICK_LINE)).toBeTruthy();

    // The question drops out of the list: the parent reads again, and the open page stays.
    groupsNow = [];
    fireEvent.click(await screen.findByRole("button", { name: "Only what everyone knows" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Let me choose" })).toBeNull());
    expect(screen.getByText(PICK_LINE)).toBeTruthy();
    expect(screen.getByRole("button", { name: "↩️ Undo" })).toBeTruthy();
    expect(screen.getByText("Choose what Mira knows. Nothing changes until you pick.")).toBeTruthy();

    // Close reads the list again (another question is now waiting) and closes the page.
    groupsNow = [{ group: "g2", answer: "waiting", names: ["Tavin"] }];
    const readsBeforeClose = reads;
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(reads).toBeGreaterThan(readsBeforeClose));
    expect(screen.queryByText("Choose what Mira knows. Nothing changes until you pick.")).toBeNull();

    // Another question's page starts with none of the first one's picks.
    fireEvent.click(await screen.findByRole("button", { name: "Let me choose" }));
    expect(await screen.findByText("Choose what Tavin knows. Nothing changes until you pick.")).toBeTruthy();
    expect(screen.queryByText(PICK_LINE)).toBeNull();
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });
});

describe("focus moves with the page (#4891)", () => {
  it("opening the page focuses its heading; after a pick the new line takes focus; Close returns focus to the button that opened it", async () => {
    const { api } = choiceApi();
    api.joinQuestions = async () => [{ group: GROUP, answer: "waiting", names: ["Mira"] }];
    render(<JoinQuestions api={api} campaignRef={REF} id="jq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Let me choose" }));
    await screen.findByText("Choose what Mira knows. Nothing changes until you pick.");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Let me choose" })));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    const line = await screen.findByText(PICK_LINE);
    await waitFor(() => expect(document.activeElement).toBe(line));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Let me choose" })));
  });
});

describe("the wait, and the rarer lines (#4891)", () => {
  it("the working line counts the seconds, says 'still working' from 20 seconds, and keeps Look again there", async () => {
    vi.useFakeTimers();
    const { api } = choiceApi({ ask: () => new Promise<void>(() => undefined) });
    render(<JoinChoose api={api} campaignRef={REF} group={GROUP} names={["Mira"]} back={false} onClose={() => undefined} />);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText(/0 seconds so far\./)).toBeTruthy();
    await vi.advanceTimersByTimeAsync(1000);
    expect(screen.getByText(/1 second so far\./)).toBeTruthy();
    await vi.advanceTimersByTimeAsync(19000);
    expect(screen.getByText(/20 seconds so far\./)).toBeTruthy();
    expect(screen.getByText(/Still working\. This can take a while\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Look again" })).toBeTruthy();
  });

  it("the nobody line", async () => {
    vi.useFakeTimers();
    const nobody: JoinChoices = { madeAt: 1_800_000_000, choices: { ...mira.choices!, nobody: true } };
    const { api } = choiceApi({ choices: nobody });
    render(<JoinChoose api={api} campaignRef={REF} group={GROUP} names={["Mira"]} back={false} onClose={() => undefined} />);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("No one else in the party is caught up yet, so there's nothing to give Mira yet. Answer the other characters' questions first.")).toBeTruthy();
  });

  it("the thread-cut line when more threads exist than are listed", async () => {
    const cut: JoinChoices = { madeAt: 1_800_000_000, choices: { ...mira.choices!, threadsInAll: 30 } };
    const { api } = choiceApi({ choices: cut });
    render(<JoinChoose api={api} campaignRef={REF} group={GROUP} names={["Mira"]} back={false} onClose={() => undefined} />);
    expect(await screen.findByText("Showing the 25 story threads with the most to give. For the rest, use 👁️ Who knows this? on any card.")).toBeTruthy();
  });
});

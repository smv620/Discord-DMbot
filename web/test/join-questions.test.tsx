/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The join question on the Questions page (#4778 part b): one message at a time, "Only what everyone knows" and "What the party
// knows" with ↩ Undo, the plural words, a second answer that is "gone", and nothing for anyone but a DM.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type JoinGroup } from "../src/account/api";
import namesArea from "../src/account/api-names";
import JoinQuestions from "../src/account/JoinQuestions";
import { mockApi } from "../src/account/mock";
import { joinWords as w } from "../src/content/join-questions-words";

afterEach(() => cleanup());

const REF = "100.cmp1";

/** A card on a campaign whose join questions are `groups` (the mock's own, unless a test gives its own). */
function open(groups?: JoinGroup[]) {
  const api = mockApi("table");
  if (groups) api.joinQuestions = async () => groups;
  render(<JoinQuestions api={api} campaignRef={REF} id="join" />);
  return api;
}

describe("the join question on the Questions page", () => {
  it("shows one message's names and both buttons", async () => {
    open();
    expect(await screen.findByText(w.heading(["Mira"]))).toBeTruthy();
    expect(screen.getByText(w.question(["Mira"]))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.everyone })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.party })).toBeTruthy();
  });

  it("the plural words for two names", async () => {
    open([{ group: "e2".repeat(16), answer: "waiting", names: ["Tavin", "Oskar"] }]);
    expect(await screen.findByText("Tavin and Oskar have joined the party.")).toBeTruthy();
    expect(screen.getByText("What do Tavin and Oskar know about what came before?")).toBeTruthy();
    expect(screen.getByText(w.explain(["Tavin", "Oskar"]))).toBeTruthy();
  });

  it("Only what everyone knows shows its line and no Undo, and the group then offers only the party answer", async () => {
    const api = open();
    fireEvent.click(await screen.findByRole("button", { name: w.everyone }));
    expect(await screen.findByText(w.everyoneDone("Mira", true))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("answerJoin:") && c.includes(":everyone:"))).toBe(true);
    expect(await screen.findByText(w.everyoneChosen)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.everyone })).toBeNull();
    expect(screen.getByRole("button", { name: w.party })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("What the party knows shows its line and an Undo, and Undo shows the undone line", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: w.party }));
    expect(await screen.findByText(w.partyDone("Mira", 3, true))).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone("Mira", true))).toBeTruthy();
  });

  it("undoFor joins names as joinOf does", () => {
    expect(w.undoFor(["Tavin"])).toBe('Undo the last "What the party knows", for Tavin.');
    expect(w.undoFor(["Tavin", "Wren"])).toBe('Undo the last "What the party knows", for Tavin and Wren.');
    expect(w.undoFor(["Tavin", "Wren", "Ash"])).toBe('Undo the last "What the party knows", for Tavin, Wren and Ash.');
  });

  it("the outcome line uses the given count, with '1 thing' for one and 'N things' otherwise", () => {
    expect(w.partyDone("Mira", 3, true)).toBe("✅ Mira now knows the 3 things the party knew.");
    expect(w.partyDone("Mira", 1, true)).toBe("✅ Mira now knows the 1 thing the party knew.");
    expect(w.partyDone("Tavin and Oskar", 2, false)).toBe("✅ Tavin and Oskar now know the 2 things the party knew.");
  });

  it("a second answer to the same group says it was already answered and reads the list again", async () => {
    const api = open();
    api.answerJoin = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(await screen.findByRole("button", { name: w.party }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    await waitFor(() => expect(api.calls.filter((c) => c === "joinQuestions:100.cmp1").length).toBeGreaterThan(1));
  });

  it("a player's page says it failed and offers no answer, since the door answers no names to anyone but a DM", async () => {
    const api = mockApi("table");
    render(<JoinQuestions api={api} campaignRef="100.cmp1.web-player" id="join" />);
    expect(await screen.findByText(w.failed)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.party })).toBeNull();
  });

  it("nothing to give shows its own line, and a single thing says '1 thing'", async () => {
    const api = open();
    api.answerJoin = async () => ({ batch: null, given: null });
    fireEvent.click(await screen.findByRole("button", { name: w.party }));
    expect(await screen.findByText(w.nothingToGive("Mira"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("a party answer that gave one thing says '1 thing' and offers its Undo", async () => {
    const api = open();
    api.answerJoin = async () => ({ batch: 5, given: 1 });
    fireEvent.click(await screen.findByRole("button", { name: w.party }));
    expect(await screen.findByText(w.partyDone("Mira", 1, true))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("an Undo that is too late, or whose message changed since, says so in words", async () => {
    const api = open();
    fireEvent.click(await screen.findByRole("button", { name: w.party }));
    expect(await screen.findByText(w.partyDone("Mira", 3, true))).toBeTruthy();
    api.answerJoin = async () => {
      throw new ApiError("changed");
    };
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.tooLate)).toBeTruthy();
  });

  it("the answer and undo calls go to the door's routes with the right bodies", async () => {
    const sent: unknown[][] = [];
    const call = async (method: string, path: string, body?: unknown) => {
      sent.push([method, path, body]);
      return { answer: "A1" };
    };
    const waitForAnswer = async () => ({ state: "done" as const, batch: 61, given: 3 });
    const api = namesArea({ call, waitForAnswer, root: "" } as never) as {
      answerJoin: (ref: string, change: unknown) => Promise<{ batch: number | null; given: number | null }>;
    };
    expect(await api.answerJoin("100.cmp1", { action: "party", group: "e1".repeat(16) })).toEqual({ batch: 61, given: 3 });
    await api.answerJoin("100.cmp1", { action: "undo", batch: 61 });
    expect(sent).toEqual([
      ["POST", "/me/campaigns/100.cmp1/join-questions/answer", { group: "e1".repeat(16), answer: "party" }],
      ["POST", "/me/campaigns/100.cmp1/join-questions/61/undo", undefined],
    ]);
  });
});

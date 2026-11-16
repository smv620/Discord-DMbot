/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The quest sheet's edit controls (#4562): a state, show or take-off press, the words, the adds and Undo each send the right change and
// say what they did, a refusal says so, and nothing shows while the switch is off or the API has no change.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ThreadCard, type ThreadCardPart } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import QuestSheet from "../src/account/QuestSheet";
import QuestSheetEdit from "../src/account/QuestSheetEdit";
import { questEditWords as w } from "../src/content/quest-edit-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/quest-edit-switch", () => ({
  get QUEST_EDIT_LIVE() {
    return live.on;
  },
}));
vi.mock("../src/content/quest-sheet-switch", () => ({ QUEST_SHEET_LIVE: true }));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "100.cmp1";
const part = (id: string, kind: string, words: string, state: string, extra: Partial<ThreadCardPart> = {}): ThreadCardPart => ({
  id, kind, words, state, underId: null, who: null, about: null, amount: null, unit: "", owed: null, due: "",
  terms: "", limitKind: "", dated: false, fromRead: false, tableKnows: false, ...extra,
});
const card = (parts: ThreadCardPart[] = [part("p4", "objective", "Find the heir", "done"), part("p5", "objective", "Cross the pass", "to_do")]): ThreadCard => ({
  thread: { id: "t1", name: "Bring the heir home", kind: "Quest", state: "active", stateWords: "Happening now", description: "" },
  people: [],
  waiting: 0,
  done: 1,
  of: parts.length,
  parts,
  bigger: null,
  branches: [],
});
const edit = (c: ThreadCard, change = vi.fn(async () => ({ batch: 1 as number | null }))) => {
  const reload = vi.fn(async () => {});
  render(<QuestSheetEdit api={{ changeThreadParts: change } as never} refId={DM} threadId="t1" card={c} reload={reload} />);
  return { change, reload };
};

describe("the quest sheet's edit controls", () => {
  it("a state button sends the part and the state and says what it did, then reads the sheet again", async () => {
    const { change, reload } = edit(card());
    fireEvent.click(screen.getByRole("button", { name: "☑ Done" }));
    expect(await screen.findByText("✅ Done: Cross the pass.")).toBeTruthy();
    expect(change).toHaveBeenCalledWith(DM, "t1", { action: "state", part: "p5", state: "done" });
    expect(reload).toHaveBeenCalled();
  });

  it("each part's words sit next to its own Change block", () => {
    edit(card([part("p4", "objective", "Find the heir", "done"), part("p5", "objective", "Cross the pass", "to_do")]));
    const blocks = screen.getAllByText(w.change, { selector: "summary" });
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.closest("details")!.parentElement!.querySelector("p")!.textContent).toBe("Find the heir");
    expect(blocks[1]!.closest("details")!.parentElement!.querySelector("p")!.textContent).toBe("Cross the pass");
  });

  it("the current state has no button", () => {
    edit(card([part("p4", "objective", "Find the heir", "done")]));
    expect(screen.queryByRole("button", { name: "☑ Done" })).toBeNull();
    expect(screen.getByRole("button", { name: "☐ To do" })).toBeTruthy();
  });

  it("Show to players and Hide from players follow tableKnows", async () => {
    const { change } = edit(card([part("p1", "reward", "50 gold", "owed", { tableKnows: true })]));
    fireEvent.click(screen.getByRole("button", { name: "Hide from players" }));
    expect(await screen.findByText("✅ Hidden from players: 50 gold.")).toBeTruthy();
    expect(change).toHaveBeenCalledWith(DM, "t1", { action: "show", part: "p1", shown: false });
  });

  it("Take it off sends remove", async () => {
    const { change } = edit(card([part("p2", "reward", "A route map", "owed")]));
    fireEvent.click(screen.getByRole("button", { name: "Take it off" }));
    expect(await screen.findByText("✅ Taken off: A route map.")).toBeTruthy();
    expect(change).toHaveBeenCalledWith(DM, "t1", { action: "remove", part: "p2" });
  });

  it("new words send fix, the box sends words_story, and Save is off for empty or unchanged words", async () => {
    const { change } = edit(card([part("p2", "reward", "A route map", "owed")]));
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    const input = screen.getByLabelText(w.newWords) as HTMLInputElement;
    fireEvent.input(input, { target: { value: "A map of the pass" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(save);
    expect(await screen.findByText("✅ Changed: A map of the pass.")).toBeTruthy();
    expect(change).toHaveBeenCalledWith(DM, "t1", { action: "fix", part: "p2", field: "words_story", value: "A map of the pass" });
  });

  it("adding a step sends kind objective and the words", async () => {
    const { change } = edit(card());
    fireEvent.change(screen.getByLabelText(w.whatToAdd), { target: { value: "objective" } });
    fireEvent.input(screen.getByLabelText("The step"), { target: { value: "Ask the guard" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    expect(await screen.findByText("✅ Added: Ask the guard.")).toBeTruthy();
    expect(change).toHaveBeenCalledWith(DM, "t1", { action: "add", kind: "objective", words: "Ask the guard" });
  });

  it("a reward sends due, help sends state, a time limit sends limit, and Not said sends nothing for it", async () => {
    const { change } = edit(card());
    fireEvent.change(screen.getByLabelText(w.whatToAdd), { target: { value: "reward" } });
    fireEvent.input(screen.getByLabelText("The reward"), { target: { value: "100 gold" } });
    fireEvent.change(screen.getByLabelText(w.whenGiven), { target: { value: "up_front" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    await screen.findByText("✅ Added: 100 gold.");
    expect(change).toHaveBeenLastCalledWith(DM, "t1", { action: "add", kind: "reward", words: "100 gold", due: "up_front" });
    fireEvent.change(screen.getByLabelText(w.whatToAdd), { target: { value: "help" } });
    fireEvent.input(screen.getByLabelText("What was handed over"), { target: { value: "A team of sleds" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    await waitFor(() => expect(change).toHaveBeenLastCalledWith(DM, "t1", { action: "add", kind: "help", words: "A team of sleds", state: "to_keep" }));
    fireEvent.change(screen.getByLabelText(w.whatToAdd), { target: { value: "time_limit" } });
    fireEvent.input(screen.getByLabelText("The time limit, in their words"), { target: { value: "Before the new moon" } });
    fireEvent.change(screen.getByLabelText(w.whenMissed), { target: { value: "hard" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    await waitFor(() => expect(change).toHaveBeenLastCalledWith(DM, "t1", { action: "add", kind: "time_limit", words: "Before the new moon", limit: "hard" }));
    fireEvent.change(screen.getByLabelText(w.whatToAdd), { target: { value: "time_limit" } });
    fireEvent.input(screen.getByLabelText("The time limit, in their words"), { target: { value: "Before dusk" } });
    expect((screen.getByLabelText(w.whenMissed) as HTMLSelectElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    await waitFor(() => expect(change).toHaveBeenLastCalledWith(DM, "t1", { action: "add", kind: "time_limit", words: "Before dusk" }));
  });

  it("Undo sends the batch as text and says Undone", async () => {
    const { change } = edit(card());
    fireEvent.click(screen.getByRole("button", { name: "☑ Done" }));
    await screen.findByText("✅ Done: Cross the pass.");
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(change).toHaveBeenLastCalledWith(DM, "t1", { action: "undo", batch: "1" });
  });

  it("a gone answer says so and reads the sheet again; any other failure says it did not save", async () => {
    const gone = vi.fn(async () => {
      throw new ApiError("gone");
    });
    const first = edit(card(), gone);
    fireEvent.click(screen.getByRole("button", { name: "☑ Done" }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    expect(first.reload).toHaveBeenCalled();
    cleanup();
    const other = edit(
      card(),
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "☑ Done" }));
    expect(await screen.findByText(w.failed)).toBeTruthy();
    expect(other.reload).toHaveBeenCalled();
  });

  it("a second press while one works does nothing", async () => {
    let release!: () => void;
    const change = vi.fn(() => new Promise<{ batch: number | null }>((r) => (release = () => r({ batch: 1 }))));
    edit(card(), change);
    const button = screen.getByRole("button", { name: "☑ Done" });
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
    release();
    await screen.findByText("✅ Done: Cross the pass.");
    expect(change).toHaveBeenCalledTimes(1);
  });
});

describe("the edit controls show only when they should", () => {
  const sheetApi = (withChange: boolean) => {
    const api = mockApi("table");
    api.threadCard = async () => card();
    if (!withChange) delete api.changeThreadParts;
    return api;
  };

  it("nothing shows while QUEST_EDIT_LIVE is off", async () => {
    live.on = false;
    render(<QuestSheet api={sheetApi(true)} refId={DM} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={() => {}} />);
    expect(await screen.findByText("Quest · Happening now")).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.add })).toBeNull();
  });

  it("nothing shows when the API has no changeThreadParts", async () => {
    render(<QuestSheet api={sheetApi(false)} refId={DM} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={() => {}} />);
    expect(await screen.findByText("Quest · Happening now")).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.add })).toBeNull();
  });
});

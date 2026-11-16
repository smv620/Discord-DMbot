/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The quest sheet in the Story Graph's detail pane (#4547): its lines, its names, the steps and the questions waiting, and nothing for a
// refused sheet. The map shows it for a thread only when the switch is on, the viewer is a DM and the API has the card.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ThreadCard, type ThreadCardPart } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import QuestSheet from "../src/account/QuestSheet";
import WebMap from "../src/account/WebMap";
import type { WebNode } from "../src/account/api";
import { questSheetWords as w } from "../src/content/quest-sheet-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/quest-sheet-switch", () => ({
  get QUEST_SHEET_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "100.cmp1";
const part = (id: string, kind: string, words: string, state: string, extra: Partial<ThreadCardPart> = {}): ThreadCardPart => ({
  id, kind, words, state, underId: null, who: null, about: null, amount: null, unit: "", owed: null, due: "",
  terms: "", limitKind: "", dated: false, fromRead: false, tableKnows: false, ...extra,
});
const card = (over: Partial<ThreadCard> = {}): ThreadCard => ({
  thread: { id: "t1", name: "Bring the heir home", kind: "Quest", state: "active", stateWords: "Happening now", description: "Bring the heir home." },
  people: [
    { id: "n-keegan", name: "Keegan Velryn", role: "quest giver" },
    { id: "n-garret", name: "Garret Velryn", role: "target" },
  ],
  waiting: 2,
  done: 1,
  of: 2,
  parts: [
    part("p1", "reward", "50 gold", "owed", { amount: 50, unit: "gold", owed: 0, due: "up_front" }),
    part("p2", "reward", "A route map", "owed"),
    part("p3", "help", "A team of sleds", "to_keep"),
    part("p4", "objective", "Find the heir", "done"),
    part("p5", "objective", "Cross the pass", "to_do"),
  ],
  bigger: null,
  branches: [],
  ...over,
});
const sheet = (c: ThreadCard, onSelect = () => {}) => {
  const api = mockApi("table");
  api.threadCard = async () => c;
  render(<QuestSheet api={api} refId={DM} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={onSelect} />);
};

describe("the quest sheet", () => {
  it("shows the heading line, the description, who is in it and the reward", async () => {
    sheet(card());
    expect(await screen.findByText("Quest · Happening now")).toBeTruthy();
    expect(screen.getByText("Bring the heir home.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Keegan Velryn" }).closest("li")!.textContent).toBe("🙋 Given by: Keegan Velryn");
    expect(screen.getByRole("button", { name: "Garret Velryn" }).closest("li")!.textContent).toBe("🎯 Target: Garret Velryn");
    expect(screen.getByText("💰 50 gold, given up front, nothing owed")).toBeTruthy();
    expect(screen.getByText("🎁 A route map")).toBeTruthy();
    expect(screen.getByText(w.helpLead)).toBeTruthy();
    expect(screen.getByText("A team of sleds (to keep)")).toBeTruthy();
  });

  it("a thread with no description says so and links to the editor", async () => {
    sheet(card({ thread: { id: "t1", name: "Bring the heir home", kind: "Quest", state: "active", stateWords: "Happening now", description: "" } }));
    expect(await screen.findByText(w.noSummary, { exact: false })).toBeTruthy();
    const link = screen.getByRole("link", { name: w.writeOne });
    expect(link.getAttribute("href")).toBe(`/account/names/${DM}#said=${encodeURIComponent("Bring the heir home")}`);
  });

  it("a person's name selects that node", async () => {
    const onSelect = vi.fn();
    sheet(card(), onSelect);
    fireEvent.click(await screen.findByRole("button", { name: "Garret Velryn" }));
    expect(onSelect).toHaveBeenCalledWith("n-garret");
  });

  it("no reward and no help says No reward stated", async () => {
    sheet(card({ parts: [part("p4", "objective", "Find the heir", "done")], done: 1, of: 1 }));
    expect(await screen.findByText(w.noReward)).toBeTruthy();
  });

  it("steps are counted, show their states and indent a sub-step", async () => {
    sheet(
      card({
        parts: [
          part("p4", "objective", "Find the heir", "done"),
          part("p5", "objective", "Cross the pass", "to_do"),
          part("p6", "objective", "Ask the guard", "to_do", { underId: "p5" }),
        ],
        done: 1,
        of: 3,
      }),
    );
    expect(await screen.findByText("1 of 3 done")).toBeTruthy();
    expect(screen.getByText("Find the heir (done)")).toBeTruthy();
    expect(screen.getByText("Cross the pass (to do)")).toBeTruthy();
    expect(screen.getByText("Ask the guard (to do)").closest("li")!.getAttribute("style")).toContain("padding-left");
  });

  it("a time limit shows dated", async () => {
    sheet(card({ parts: [part("p7", "time_limit", "The pass closes at dusk", "running", { dated: true })], done: 0, of: 0 }));
    expect(await screen.findByText("⏰ The pass closes at dusk (dated)")).toBeTruthy();
  });

  it("questions waiting: one, many, and none leaves the line out", async () => {
    sheet(card({ waiting: 1 }));
    expect(await screen.findByText("❓ 1 question waiting on this quest")).toBeTruthy();
    cleanup();
    sheet(card({ waiting: 3 }));
    expect(await screen.findByText("❓ 3 questions waiting on this quest")).toBeTruthy();
    cleanup();
    sheet(card({ waiting: 0 }));
    expect(await screen.findByText("Quest · Happening now")).toBeTruthy();
    expect(screen.queryByText(/waiting on this quest/)).toBeNull();
  });

  it("a refused sheet shows nothing", async () => {
    const api = mockApi("table");
    api.threadCard = async () => {
      throw new ApiError("not-allowed");
    };
    const { container } = render(<QuestSheet api={api} refId={DM} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={() => {}} />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });
});

describe("the map's detail pane shows the sheet for a thread", () => {
  const nodes: WebNode[] = [{ id: "t", name: "Bring the heir home", kind: "thread" }];
  const draw = (canEdit: boolean, api: ReturnType<typeof mockApi>) =>
    render(<WebMap nodes={nodes} edges={[]} center="t" refId={DM} selected={null} onCenter={() => {}} onLine={() => {}} canEdit={canEdit} api={api} />);

  it("shows the sheet for a DM when the switch is on and the API has the card", async () => {
    const api = mockApi("table");
    api.threadCard = async () => card({ thread: { id: "t", name: "Bring the heir home", kind: "Quest", state: "active", stateWords: "Happening now", description: "" } });
    draw(true, api);
    expect(await screen.findByText("Quest · Happening now", {}, { timeout: 4000 })).toBeTruthy();
  });

  it("shows nothing while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    const calls = vi.fn(async () => card());
    api.threadCard = calls;
    draw(true, api);
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).not.toHaveBeenCalled();
    expect(screen.queryByText("Quest · Happening now")).toBeNull();
  });

  it("shows nothing for a viewer who is not a DM", async () => {
    const api = mockApi("table");
    const calls = vi.fn(async () => card());
    api.threadCard = calls;
    draw(false, api);
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).not.toHaveBeenCalled();
  });
});

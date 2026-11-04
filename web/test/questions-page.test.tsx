/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Questions page (#3438): every question DMbot waits on, by kind, for each campaign the person runs. It shows
// counts and links only; a kind it cannot answer yet says how to answer it in Discord, and an unknown kind is never
// dropped. The switch is mocked on here; off, nothing shows.
import { cleanup, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Questions, { CATCHING_UP_SECONDS, oldestFirst } from "../src/account/Questions";
import QuestionsLink from "../src/account/QuestionsLink";
import { kindWords, questionsPage as w } from "../src/content/questions-words";
import type { QuestionCounts } from "../src/account/api";

const on = vi.hoisted(() => ({ live: true }));
vi.mock("../src/content/questions-switch", () => ({
  get QUESTIONS_LIVE() {
    return on.live;
  },
}));

afterEach(() => {
  cleanup();
  on.live = true;
});

const now = 1_800_000_000;
const counts = (over: Partial<QuestionCounts["campaigns"][number]> = {}): QuestionCounts => ({
  serverNow: now,
  campaigns: [{ ref: "100.cmp1", name: "The Ashen Road", total: 5, countedAt: now - 30, kinds: [{ kind: "names", count: 5, oldest: now - 100 }], ...over }],
});

describe("the Questions page", () => {
  it("shows nothing while the switch is off", async () => {
    on.live = false;
    const api = mockApi("table");
    const { container } = render(<Questions api={api} />);
    expect(container.textContent).toBe("");
    expect(api.calls.filter((c) => c === "myQuestions")).toHaveLength(0);
  });

  it("lists each campaign with a line for each kind, oldest first, and the page that answers it", async () => {
    render(<Questions api={mockApi("table")} />);
    const campaign = (await screen.findByRole("heading", { name: "The Ashen Road" })).closest("section") as HTMLElement;
    const lines = within(campaign).getAllByRole("listitem");
    expect(lines.map((l) => l.querySelector("strong")?.textContent)).toEqual([
      `${kindWords("names").label} · 3`,
      `${kindWords("connections").label} · 12`,
      `${kindWords("family").label} · 2`,
    ]); // (oldest first, by when each kind's oldest question began)
  });

  it("a kind that links goes to the page that answers it, and one that can't says how in Discord", async () => {
    render(<Questions api={mockApi("table")} />);
    await screen.findByRole("heading", { name: "The Ashen Road" });
    const names = screen.getByText(`${kindWords("names").label} · 3`).closest("li") as HTMLElement;
    expect(within(names).getByRole("link").getAttribute("href")).toBe("/account/recaps?campaign=100.cmp1");
    const family = screen.getByText(`${kindWords("family").label} · 2`).closest("li") as HTMLElement;
    expect(within(family).queryByRole("link")).toBeNull();
    expect(within(family).getByText(w.discord)).toBeTruthy();
  });

  it("a campaign with nothing waiting says so", async () => {
    render(<Questions api={mockApi("table")} />);
    const empty = (await screen.findByRole("heading", { name: "Salt and Smoke" })).closest("section") as HTMLElement;
    expect(within(empty).getByText(w.nothing)).toBeTruthy();
  });

  it("a kind the page has no words for is still shown, never dropped", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts({ kinds: [{ kind: "something_new", count: 4, oldest: now - 5 }], total: 4 });
    render(<Questions api={api} />);
    expect(await screen.findByText(`${w.otherKind.label} · 4`)).toBeTruthy();
    expect(screen.getByText(w.discord)).toBeTruthy();
  });

  it("says the numbers are old when the bot has not counted for five minutes", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts({ countedAt: now - CATCHING_UP_SECONDS - 1 });
    render(<Questions api={api} />);
    expect(await screen.findByText(w.catchingUp)).toBeTruthy();
  });

  it("does not say it while the counts are fresh", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts();
    render(<Questions api={api} />);
    await screen.findByRole("heading", { name: "The Ashen Road" });
    expect(screen.queryByText(w.catchingUp)).toBeNull();
  });

  it("says so when the questions can't be loaded", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => {
      throw new Error("down");
    };
    render(<Questions api={api} />);
    expect(await screen.findByText(w.failed)).toBeTruthy();
  });

  it("orders kinds oldest first and leaves out kinds with nothing waiting", () => {
    const sorted = oldestFirst([
      { kind: "b", count: 1, oldest: 20 },
      { kind: "a", count: 2, oldest: 10 },
      { kind: "z", count: 0, oldest: 1 },
      { kind: "n", count: 3, oldest: null },
    ]);
    expect(sorted.map((k) => k.kind)).toEqual(["a", "b", "n"]);
  });

  it("every known kind has a label, and a link names where it goes", () => {
    for (const [key, kind] of Object.entries(w.kinds)) {
      expect(kind.label, key).toBeTruthy();
      if (kind.how === "link") {
        expect(kind.to, key).toBeTruthy();
        expect(kind.link, key).toBeTruthy();
      }
    }
  });
});

describe("the count on the links", () => {
  it("shows a link with the count while questions wait", async () => {
    render(<QuestionsLink api={mockApi("table")} />);
    const link = await screen.findByRole("link", { name: new RegExp(w.linkLabel) });
    expect(link.getAttribute("href")).toBe("/account/questions");
    expect(link.textContent).toContain("(17)");
  });

  it("shows nothing for a player (the page says not to ask)", () => {
    const api = mockApi("table");
    const { container } = render(<QuestionsLink api={api} show={false} />);
    expect(container.textContent).toBe("");
    expect(api.calls).not.toContain("questionsTotal");
  });

  it("shows nothing with nothing waiting", async () => {
    const api = mockApi("table");
    api.questionsTotal = vi.fn(async () => ({ total: 0 }));
    const { container } = render(<QuestionsLink api={api} />);
    await waitFor(() => expect(api.questionsTotal).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("shows nothing when the count can't be read (no badge is better than a wrong one)", async () => {
    const api = mockApi("table");
    api.questionsTotal = vi.fn(async () => {
      throw new Error("down");
    });
    const { container } = render(<QuestionsLink api={api} />);
    await waitFor(() => expect(api.questionsTotal).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("shows nothing while the switch is off", () => {
    on.live = false;
    expect(render(<QuestionsLink api={mockApi("table")} />).container.textContent).toBe("");
  });
});

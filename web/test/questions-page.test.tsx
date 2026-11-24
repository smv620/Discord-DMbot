/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Questions page (#3438): every question DMbot waits on, by kind, for each campaign the person runs. It shows
// counts and links only; a kind it cannot answer yet says how to answer it in Discord, and an unknown kind is never
// dropped. The switch is mocked on here; off, nothing shows.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Questions, { CATCHING_UP_SECONDS, inFixedOrder, mergeUnknown, oldestFirst } from "../src/account/Questions";
import QuestionsIsland from "../src/account/QuestionsIsland";
import QuestionsNames from "../src/account/QuestionsNames";
import QuestionsLink from "../src/account/QuestionsLink";
import { kindWords, questionsPage as w } from "../src/content/questions-words";
import type { QuestionCounts } from "../src/account/api";

const on = vi.hoisted(() => ({ live: true }));
vi.mock("../src/content/questions-switch", () => ({
  get QUESTIONS_LIVE() {
    return on.live;
  },
}));

// The cards that other pages answer stay off here, as they were before their switches went on (#4841): these tests count
// and route only the kinds each one turns on, and the switches' own pages have their own tests.
vi.mock("../src/content/heard-switch", () => ({ HEARD_LIVE: false }));
vi.mock("../src/content/rule-updates-switch", () => ({ RULE_UPDATES_LIVE: false }));
vi.mock("../src/content/world-notes-switch", () => ({ WORLD_NOTES_LIVE: false }));
vi.mock("../src/content/threads-switch", () => ({ THREADS_LIVE: false }));
vi.mock("../src/content/maps-switch", () => ({ MAPS_LIVE: false }));
vi.mock("../src/content/trait-questions-switch", () => ({ TRAIT_QUESTIONS_LIVE: false }));
vi.mock("../src/content/confirm-kinds-switch", () => ({ CONFIRM_KINDS_LIVE: false }));

// The 🤫 Known or secret? question is answered on the page only while its switch is on (#4598).
const secrets = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/secret-questions-switch", () => ({
  get SECRET_QUESTIONS_LIVE() {
    return secrets.on;
  },
}));

// DMbot's own matches are counted only where a test turns the switch on (#3701).
const matches = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/name-matches-switch", () => ({
  get NAME_MATCHES_LIVE() {
    return matches.on;
  },
}));

// The names-to-check cards are on only where a test turns them on (#3438).
const names = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return names.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

// The Story Graph's lists (waiting connections, readings) are on only where a test turns them on (#3438).
const lists = vi.hoisted(() => ({ waiting: false, readings: false }));
vi.mock("../src/content/web-waiting", async (original) => {
  const real = await original<typeof import("../src/content/web-waiting")>();
  return {
    ...real,
    get WEB_WAITING_LIVE() {
      return lists.waiting;
    },
  };
});
vi.mock("../src/content/web-readings", async (original) => {
  const real = await original<typeof import("../src/content/web-readings")>();
  return {
    ...real,
    get WEB_READINGS_LIVE() {
      return lists.readings;
    },
  };
});

afterEach(() => {
  cleanup();
  on.live = true;
  names.on = false;
  lists.waiting = false;
  lists.readings = false;
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

  it("lists each campaign with a line for each kind, in the fixed order, and the page that answers it", async () => {
    render(<Questions api={mockApi("table")} />);
    const campaign = (await screen.findByRole("heading", { name: "The Ashen Road" })).closest("section") as HTMLElement;
    const lines = within(campaign).getAllByRole("listitem");
    expect(lines.map((l) => l.querySelector("strong")?.textContent)).toEqual([
      `${kindWords("names").label} · 3`,
      `${kindWords("heard").label} · 2`,
      `${kindWords("connections").label} · 12`,
    ]); // (names, then what was said, then the connections: a connection is asked once its names are answered)
  });

  it("answers the known or secret kind on the page only while its switch is on, else says how in Discord", async () => {
    const now = Math.floor(Date.now() / 1000);
    const counts = async () => ({
      serverNow: now,
      campaigns: [{ ref: "100.cmp1", name: "The Ashen Road", total: 1, countedAt: now - 30, kinds: [{ kind: "secrets", count: 1, oldest: now - 60 }] }],
    });
    secrets.on = false;
    const off = mockApi("table");
    off.myQuestions = counts as never;
    render(<Questions api={off} />);
    const row = (await screen.findByText(`${kindWords("secrets").label} · 1`)).closest("li") as HTMLElement;
    expect(within(row).queryByRole("link")).toBeNull();
    expect(within(row).getByText(w.discord)).toBeTruthy();
    cleanup();
    secrets.on = true;
    const on2 = mockApi("table");
    on2.myQuestions = counts as never;
    render(<Questions api={on2} />);
    const row2 = (await screen.findByText(`${kindWords("secrets").label} · 1`)).closest("li") as HTMLElement;
    expect(within(row2).queryByText(w.discord)).toBeNull();
    secrets.on = false;
  });

  it("a kind that links goes to the page that answers it, and one that can't says how in Discord", async () => {
    render(<Questions api={mockApi("table")} />);
    await screen.findByRole("heading", { name: "The Ashen Road" });
    const names = screen.getByText(`${kindWords("names").label} · 3`).closest("li") as HTMLElement;
    expect(within(names).getByRole("link").getAttribute("href")).toBe("/account/recaps?campaign=100.cmp1");
    const heard = screen.getByText(`${kindWords("heard").label} · 2`).closest("li") as HTMLElement;
    expect(within(heard).queryByRole("link")).toBeNull();
    expect(within(heard).getByText(w.discord)).toBeTruthy();
    expect(screen.getByText(w.discordWhere)).toBeTruthy(); // (said once, under the list)
  });

  it("shows Family ties now that the Story Graph can answer them (#3463), with a link to it", async () => {
    const api = mockApi("table");
    api.myQuestions = async () =>
      counts({ total: 7, kinds: [{ kind: "names", count: 5, oldest: now - 100 }, { kind: "family", count: 2, oldest: now - 50 }] });
    render(<Questions api={api} />);
    await screen.findByText(`${kindWords("names").label} · 5`);
    const ties = screen.getByText(`${kindWords("family").label} · 2`).closest("li") as HTMLElement;
    expect(within(ties).getByRole("link")).toBeTruthy();
    expect(screen.getByText(w.campaignLine(7))).toBeTruthy();
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

  it("never gives the all-clear for a campaign the bot has not counted", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts({ countedAt: null, total: 0, kinds: [] });
    render(<Questions api={api} />);
    expect(await screen.findByText(w.catchingUp)).toBeTruthy();
    expect(screen.queryByText(w.nothing)).toBeNull();
  });

  it("decides from the kinds: kinds shown even when the total reads 0, no kinds is the all-clear", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts({ total: 0 });
    render(<Questions api={api} />);
    expect(await screen.findByText(`${kindWords("names").label} · 5`)).toBeTruthy();
    cleanup();
    api.myQuestions = async () => counts({ total: 3, kinds: [] });
    render(<Questions api={api} />);
    expect(await screen.findByText(w.nothing)).toBeTruthy();
  });

  it("adds up the kinds it has no words for into one line", () => {
    const merged = mergeUnknown([
      { kind: "names", count: 1, oldest: 5 },
      { kind: "x1", count: 2, oldest: 9 },
      { kind: "x2", count: 3, oldest: 7 },
    ]);
    expect(merged).toEqual([
      { kind: "names", count: 1, oldest: 5 },
      { kind: "other", count: 5, oldest: 7 },
    ]);
    expect(kindWords("other").label).toBe(w.otherKind.label);
  });

  it("the page's island builds no API and asks nothing while the switch is off", () => {
    on.live = false;
    const { container } = render(<QuestionsIsland />);
    expect(container.textContent).toBe(w.notYet);
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

  it("puts names, then what was said, then facts and connections first, and the rest oldest first (#4193)", () => {
    const sorted = inFixedOrder([
      { kind: "connections", count: 1, oldest: 1 },
      { kind: "readings", count: 1, oldest: 30 },
      { kind: "claims", count: 1, oldest: 2 },
      { kind: "maps", count: 1, oldest: 10 },
      { kind: "names", count: 1, oldest: 50 },
      { kind: "which", count: 1, oldest: 3 },
      { kind: "heard", count: 1, oldest: 40 },
      { kind: "plot", count: 0, oldest: 1 },
    ]);
    expect(sorted.map((k) => k.kind)).toEqual(["names", "heard", "claims", "connections", "which", "maps", "readings"]);
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

describe("new names to check, answered on the page (#3438)", () => {
  const openNames = async (api = mockApi("table")) => {
    names.on = true;
    render(<Questions api={api} />);
    const line = (await screen.findByText(`${kindWords("names").label} · 3`)).closest("li") as HTMLElement;
    return { api, line };
  };

  it("offers to answer them here, not a link away", async () => {
    const { line } = await openNames();
    expect(within(line).getByRole("button", { name: `${w.showHere}: ${kindWords("names").label}` })).toBeTruthy();
    expect(within(line).queryByRole("link")).toBeNull();
  });

  it("shows each recap with names waiting as a session heading with its own list", async () => {
    const { api, line } = await openNames();
    fireEvent.click(within(line).getByRole("button", { name: `${w.showHere}: ${kindWords("names").label}` }));
    expect(await within(line).findByRole("heading", { name: w.sessionOf("3 January 2027") })).toBeTruthy();
    expect(api.calls).toContain("campaignNameChecks:100.cmp1");
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("nameChecks:100.cmp1.100"))).toBe(true));
  });

  it("hides them again, and says what it is doing while the read is made", async () => {
    const { line } = await openNames();
    const button = within(line).getByRole("button", { name: `${w.showHere}: ${kindWords("names").label}` });
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(within(line).getByText(w.loading)).toBeTruthy();
    fireEvent.click(within(line).getByRole("button", { name: `${w.hideHere}: ${kindWords("names").label}` }));
    expect(within(line).queryByRole("heading")).toBeNull();
  });

  it("says the names wait outside a recap, never that nothing waits, while the count is above 0 (#5186)", async () => {
    const api = mockApi("table");
    api.campaignNameChecks = async () => ({ recaps: [] });
    const { line } = await openNames(api);
    fireEvent.click(within(line).getByRole("button", { name: `${w.showHere}: ${kindWords("names").label}` }));
    const count = Number(/· (\d+)/.exec(line.textContent ?? "")?.[1]);
    expect(count).toBeGreaterThan(0);
    expect(await within(line).findByText(w.outsideRecap(count))).toBeTruthy();
    expect(within(line).queryByText(w.noneHere)).toBeNull();
  });

  it("says so when the list can't be read", async () => {
    const api = mockApi("table");
    api.campaignNameChecks = async () => {
      throw new Error("down");
    };
    const { line } = await openNames(api);
    fireEvent.click(within(line).getByRole("button", { name: `${w.showHere}: ${kindWords("names").label}` }));
    expect(await within(line).findByText(w.failed)).toBeTruthy();
  });

  it("falls back to the link while the cards are switched off or the API can't read them", async () => {
    names.on = false;
    render(<Questions api={mockApi("table")} />);
    const line = (await screen.findByText(`${kindWords("names").label} · 3`)).closest("li") as HTMLElement;
    expect(within(line).getByRole("link").getAttribute("href")).toBe("/account/recaps?campaign=100.cmp1");
    expect(within(line).queryByRole("button")).toBeNull();
    cleanup();
    names.on = true;
    const { campaignNameChecks: _gone, ...old } = mockApi("table");
    render(<Questions api={old} />);
    const again = (await screen.findByText(`${kindWords("names").label} · 3`)).closest("li") as HTMLElement;
    expect(within(again).getByRole("link")).toBeTruthy();
  });
});

describe("connections, Which one? and readings, answered on the page (#3438)", () => {
  const many = (): QuestionCounts => ({
    serverNow: now,
    campaigns: [
      {
        ref: "100.cmp1",
        name: "The Ashen Road",
        total: 9,
        countedAt: now - 30,
        kinds: [
          { kind: "connections", count: 4, oldest: now - 400 },
          { kind: "which", count: 2, oldest: now - 300 },
          { kind: "readings", count: 3, oldest: now - 200 },
          { kind: "heard", count: 1, oldest: now - 100 },
        ],
      },
    ],
  });
  const show = async () => {
    const api = mockApi("table");
    api.myQuestions = async () => many();
    lists.waiting = true;
    lists.readings = true;
    render(<Questions api={api} />);
    await screen.findByText(`${kindWords("connections").label} · 4`);
    return api;
  };
  const line = (kind: string, n: number) => screen.getByText(`${kindWords(kind).label} · ${n}`).closest("li") as HTMLElement;

  it("opens the Story Graph's waiting list under the first of its kinds, and says the others are answered with it", async () => {
    const api = await show();
    const first = line("connections", 4);
    fireEvent.click(within(first).getByRole("button", { name: `${w.showHere}: ${kindWords("connections").label}` }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webWaiting:100.cmp1"))).toBe(true));
    expect(within(line("which", 2)).getByText(w.answeredWith(kindWords("connections").label))).toBeTruthy();
    expect(within(line("which", 2)).queryByRole("button")).toBeNull();
  });

  it("opens what people believe as its own list", async () => {
    const api = await show();
    fireEvent.click(within(line("readings", 3)).getByRole("button", { name: `${w.showHere}: ${kindWords("readings").label}` }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webReadings:100.cmp1"))).toBe(true));
  });

  it("a kind the page cannot answer still says how to answer it in Discord", async () => {
    await show();
    expect(within(line("heard", 1)).getByText(w.discord)).toBeTruthy();
  });

  it("falls back to the link while the lists are switched off", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => many();
    render(<Questions api={api} />);
    await screen.findByText(`${kindWords("connections").label} · 4`);
    expect(within(line("connections", 4)).getByRole("link").getAttribute("href")).toBe("/account/web/100.cmp1");
    expect(within(line("readings", 3)).getByRole("link")).toBeTruthy();
    expect(screen.queryByText(w.answeredWith(kindWords("connections").label))).toBeNull();
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


describe("DMbot's guesses on the Questions page (#3701)", () => {
  const withGuesses = (guesses: number | undefined) => {
    const api = mockApi("table");
    const read = api.myQuestions!.bind(api);
    api.myQuestions = async () => {
      const got = await read();
      return { ...got, campaigns: got.campaigns.map((c, i) => (i === 0 ? (guesses === undefined ? (({ guesses: _gone, ...rest }) => rest)(c) : { ...c, guesses }) : c)) };
    };
    return api;
  };
  afterEach(() => {
    matches.on = false;
  });

  it("says how many wait and links to the Names page, only while the switch is on", async () => {
    matches.on = true;
    render(<Questions api={withGuesses(3)} />);
    expect(await screen.findByText(/3 guesses wait for you\./)).toBeTruthy();
    const link = screen.getByRole("link", { name: "Check DMbot's guesses" });
    expect(link.getAttribute("href")).toBe("/account/names/100.cmp1");
  });

  it("shows nothing when none wait, or the API sends no count, or the switch is off", async () => {
    matches.on = true;
    const { unmount } = render(<Questions api={withGuesses(0)} />);
    await screen.findByText("The Ashen Road");
    expect(screen.queryByText(/guess/)).toBeNull();
    unmount();
    render(<Questions api={withGuesses(undefined)} />);
    await screen.findByText("The Ashen Road");
    expect(screen.queryByRole("link", { name: "Check DMbot's guesses" })).toBeNull();
    cleanup();
    matches.on = false;
    render(<Questions api={withGuesses(3)} />);
    await screen.findByText("The Ashen Road");
    expect(screen.queryByText(/guesses? wait/)).toBeNull();
  });

  it("does not say nothing waits for a campaign that has only guesses", async () => {
    matches.on = true;
    const api = mockApi("table");
    api.myQuestions = async () => ({
      serverNow: 1000,
      campaigns: [{ ref: "100.cmp2", name: "Salt and Smoke", total: 0, countedAt: 990, kinds: [], guesses: 1 }],
    });
    render(<Questions api={api} />);
    expect(await screen.findByText("1 guess waits for you.")).toBeTruthy();
    expect(screen.queryByText(w.nothing)).toBeNull();
  });
});

describe("Check everything, one walk through what can be answered here (#4193)", () => {
  const many = (): QuestionCounts => ({
    serverNow: now,
    campaigns: [
      {
        ref: "100.cmp1",
        name: "The Ashen Road",
        total: 9,
        countedAt: now - 30,
        kinds: [
          { kind: "connections", count: 4, oldest: now - 400 },
          { kind: "which", count: 2, oldest: now - 300 },
          { kind: "readings", count: 3, oldest: now - 200 },
          { kind: "heard", count: 1, oldest: now - 100 },
        ],
      },
    ],
  });
  const show = async () => {
    const api = mockApi("table");
    api.myQuestions = async () => many();
    lists.waiting = true;
    lists.readings = true;
    render(<Questions api={api} />);
    await screen.findByText(`${kindWords("connections").label} · 4`);
    return api;
  };
  const connections = kindWords("connections").label;
  const believe = kindWords("readings").label;

  it("is one button for everything that opens here, with the total of those parts", async () => {
    await show();
    expect(screen.getByRole("button", { name: w.checkEverything(9) })).toBeTruthy(); // (the Discord-only kind is not counted)
  });

  it("has no walk when only one thing can be answered here", async () => {
    lists.waiting = false;
    lists.readings = false;
    names.on = false;
    render(<Questions api={mockApi("table")} />);
    await screen.findByRole("heading", { name: "The Ashen Road" });
    expect(screen.queryByRole("button", { name: /Check everything/ })).toBeNull();
  });

  it("starts at the first part with its progress line and opens that part's cards", async () => {
    const api = await show();
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    expect(await screen.findByText(w.partLine(connections, 1, 2, 6))).toBeTruthy();
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webWaiting:100.cmp1"))).toBe(true));
  });

  it("moves to the next part with one line saying what is done and what is next", async () => {
    const api = await show();
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    fireEvent.click(await screen.findByRole("button", { name: w.nextPart(believe, 3) }));
    expect(await screen.findByText(w.partLine(believe, 2, 2, 3))).toBeTruthy();
    expect(screen.getByText(w.partDone(connections, believe, 3))).toBeTruthy();
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webReadings:100.cmp1"))).toBe(true));
  });

  it("stops for now and carries on at the same part with what is left", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    fireEvent.click(await screen.findByRole("button", { name: w.nextPart(believe, 3) }));
    fireEvent.click(await screen.findByRole("button", { name: w.stopForNow }));
    expect(screen.getByText(w.walkStopped)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.carryOn(3) }));
    expect(await screen.findByText(w.partLine(believe, 2, 2, 3))).toBeTruthy();
  });

  it("stopping on the first part still says Carry on, with everything left", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    fireEvent.click(await screen.findByRole("button", { name: w.stopForNow }));
    fireEvent.click(screen.getByRole("button", { name: w.carryOn(9) }));
    expect(await screen.findByText(w.partLine(connections, 1, 2, 6))).toBeTruthy();
    expect(screen.queryByText(w.walkStopped)).toBeNull(); // (the old line goes when the walk starts again)
  });

  it("ends after the last part and offers the whole walk again", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    fireEvent.click(await screen.findByRole("button", { name: w.nextPart(believe, 3) }));
    fireEvent.click(await screen.findByRole("button", { name: w.finishWalk }));
    expect(screen.getByText(w.walkEnded)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.checkEverything(9) })).toBeTruthy();
  });
});

describe("the walk moves on by itself and keeps focus (#4740)", () => {
  const many = (): QuestionCounts => ({
    serverNow: now,
    campaigns: [
      {
        ref: "100.cmp1",
        name: "The Ashen Road",
        total: 9,
        countedAt: now - 30,
        kinds: [
          { kind: "connections", count: 4, oldest: now - 400 },
          { kind: "which", count: 2, oldest: now - 300 },
          { kind: "readings", count: 3, oldest: now - 200 },
          { kind: "heard", count: 1, oldest: now - 100 },
        ],
      },
    ],
  });
  const connections = kindWords("connections").label;
  const believe = kindWords("readings").label;
  const start = async (apply: (api: ReturnType<typeof mockApi>) => void = () => {}) => {
    const api = mockApi("table");
    api.myQuestions = async () => many();
    apply(api);
    lists.waiting = true;
    lists.readings = true;
    render(<Questions api={api} />);
    await screen.findByText(`${kindWords("connections").label} · 4`);
    fireEvent.click(screen.getByRole("button", { name: w.checkEverything(9) }));
    return api;
  };

  it("the count drops as answers go: the waiting part reports what is still waiting", async () => {
    await start((api) => {
      const real = api.webWaiting!.bind(api);
      api.webWaiting = async (ref) => {
        const got = await real(ref);
        return { ...got, claims: got.claims.slice(0, 1), backstory: [], which: [] };
      };
    });
    expect(await screen.findByText(w.partLine(connections, 1, 2, 1))).toBeTruthy();
  });

  it("a part that empties moves on by itself and says what is done and what is next", async () => {
    await start((api) => {
      const real = api.webWaiting!.bind(api);
      api.webWaiting = async (ref) => ({ ...(await real(ref)), claims: [], backstory: [], which: [] });
    });
    expect(await screen.findByText(w.partLine(believe, 2, 2, 3))).toBeTruthy();
    expect(screen.getByText(w.partDone(connections, believe, 3))).toBeTruthy();
  });

  it("the last part emptying ends the walk with the end line", async () => {
    await start((api) => {
      const real = api.webWaiting!.bind(api);
      api.webWaiting = async (ref) => ({ ...(await real(ref)), claims: [], backstory: [], which: [] });
      const readings = api.webReadings!.bind(api);
      api.webReadings = async (ref) => ({ ...(await readings(ref)), readings: [], counts: { readings: 0 }, capped: false });
    });
    expect(await screen.findByText(w.walkEnded)).toBeTruthy();
  });

  it("a part whose list fails to read stays until Next is pressed", async () => {
    await start((api) => {
      api.webWaiting = async () => {
        throw new Error("down");
      };
    });
    expect(await screen.findByText(w.partLine(connections, 1, 2, 6))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.nextPart(believe, 3) })).toBeTruthy();
  });

  it("focus is on the progress line when a part starts, and on the start button after Stop", async () => {
    await start();
    const line = (await screen.findByText(w.partLine(connections, 1, 2, 6))).closest("p") as HTMLElement;
    await waitFor(() => expect(document.activeElement).toBe(line));
    fireEvent.click(screen.getByRole("button", { name: w.stopForNow }));
    await waitFor(() => expect(document.activeElement?.textContent).toBe(w.carryOn(9)));
  });
});

describe("the names part reports from every recap (#4740)", () => {
  it("names report 0 only once every recap has reported", async () => {
    names.on = true; // (the recap's own list is on only where a test turns it on, as above)
    const api = mockApi("table");
    api.campaignNameChecks = async () => ({
      recaps: [
        { ref: "100.cmp1.a", at: 1_799_000_000, count: 0 },
        { ref: "100.cmp1.b", at: 1_799_100_000, count: 0 },
      ],
    });
    api.nameChecks = async () => ({ names: [], known: [], kinds: [] });
    const onLeft = vi.fn();
    render(<QuestionsNames api={api} campaignRef="100.cmp1" onLeft={onLeft} />);
    await waitFor(() => expect(onLeft).toHaveBeenCalledWith(0));
    expect(onLeft.mock.calls.every(([n]) => n === 0)).toBe(true);
    names.on = false;
  });
});

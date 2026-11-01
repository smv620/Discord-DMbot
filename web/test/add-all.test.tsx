/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Add all" under a finished upload on the Upload lore page (#3207, #3222), behind CONFIRM_KINDS_LIVE: the DM adds
// the names a document found, watches the wait, and can take them back. A player sees nothing; ideas are never counted.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import AddAll from "../src/account/AddAll";
import { ApiError, type AccountApi, type ConfirmAnswer, type LoreUpload } from "../src/account/api";
import { addAllWords as w, countsList, plainKindSingular, sumCounts } from "../src/content/add-all-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/confirm-kinds-switch", () => ({
  get CONFIRM_KINDS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
  vi.useRealTimers();
});

const upload = (waiting: Record<string, number>): LoreUpload => ({
  id: "up-1",
  state: "done",
  stage: "done",
  error: "",
  source: "paste",
  choice: "world",
  namesFound: 4,
  linksFound: 3,
  done: 1,
  total: 1,
  createdAt: 1,
  updatedAt: 2,
  serverNow: 3,
  waiting,
});

/** An API whose answers are scripted: each look reads the next answer in `looks`. */
function apiWith(looks: ConfirmAnswer[], over: Partial<AccountApi> = {}): AccountApi {
  let next = 0;
  return {
    confirmKinds: async () => ({ answer: "a-1" }),
    confirmUndo: async () => ({ answer: "a-2" }),
    confirmAnswer: async () => looks[Math.min(next++, looks.length - 1)]!,
    ...over,
  } as unknown as AccountApi;
}

const done = (counts: Record<string, number>, undo = true): ConfirmAnswer => ({ state: "done", stage: "done", counts, undo });

describe("the words for the counts", () => {
  it("reads the bot's text and names the biggest kinds", () => {
    expect(sumCounts({ place: 5, character: 7 })).toBe(12);
    expect(countsList({ place: 5, character: 7 })).toBe("7 characters and 5 places");
    expect(countsList({ place: 5, character: 7, group: 2 })).toBe("7 characters and 5 places, and 1 other kind");
    expect(countsList({ place: 5, character: 7, group: 2, item: 1 })).toBe("7 characters and 5 places, and 2 other kinds");
    expect(w.found(1)).toBe("1 name found in your document is waiting for your OK.");
    expect(w.found(3)).toBe("3 names found in your document are waiting for your OK.");
  });
});

describe("the buttons", () => {
  it("are in order: all the names, the biggest four kinds, then check one by one", async () => {
    const { container } = render(
      <AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 5, character: 7, group: 2, item: 3, npc: 1 })} />,
    );
    const buttons = Array.from(container.querySelectorAll("button")).map((b) => b.textContent);
    // The biggest four kinds: the one-name kind is the fifth biggest, so it is left out of the buttons.
    expect(buttons).toEqual([w.addAll(18), "Add all 7 characters", "Add all 5 places", "Add all 3 items", "Add all 2 groups"]);
    expect(container.querySelector("a")?.textContent).toBe(w.checkOneByOne);
  });

  it("with one kind, shows no kind buttons; with one name, says Add 1 name alone", () => {
    const one = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 3 })} />);
    expect(Array.from(one.container.querySelectorAll("button")).map((b) => b.textContent)).toEqual([w.addAll(3)]);
    cleanup();
    const single = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 1 })} />);
    expect(Array.from(single.container.querySelectorAll("button")).map((b) => b.textContent)).toEqual([w.addOne]);
  });

  it("always says the ideas are not included", () => {
    render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 2 })} />);
    expect(screen.getByText(w.ideas)).toBeTruthy();
  });

  it("with nothing waiting, has no add buttons and says so", () => {
    const { container } = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({})} />);
    expect(screen.getByText(w.nothingLeft)).toBeTruthy();
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });
});

describe("the press and its wait", () => {
  it("running disables the buttons and shows the wait", async () => {
    const running: ConfirmAnswer = { state: "working", stage: "adding", progress: { done: 1, total: 2, at: 10, createdAt: 1, now: 10 } };
    const { container } = render(
      <AddAll api={apiWith([running, running, done({ place: 5 })])} campaign="100.cmp1" upload={upload({ place: 5 })} />,
    );
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.adding)).toBeTruthy();
    expect(await screen.findByText(w.part(1, 2))).toBeTruthy();
    expect((container.querySelector("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("done shows the counts, and Undo takes them back", async () => {
    const api = apiWith([done({ place: 5, character: 7 })], {
      confirmAnswer: async () => done({ place: 5, character: 7 }),
    });
    render(<AddAll api={api} campaign="100.cmp1" upload={upload({ place: 5, character: 7 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(12) }));
    expect(await screen.findByText(w.added(12, "7 characters and 5 places"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone(12))).toBeTruthy();
  });

  it("a failed press with counts says how many were added, and without counts says none", async () => {
    const failed = (counts?: Record<string, number>): ConfirmAnswer =>
      counts === undefined ? { state: "failed", stage: "failed", error: "x" } : { state: "failed", stage: "failed", error: "x", counts };
    render(<AddAll api={apiWith([failed({ place: 2 })], { confirmAnswer: async () => failed({ place: 2 }) })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.failedSome(2, 5))).toBeTruthy();
    cleanup();
    render(<AddAll api={apiWith([failed()], { confirmAnswer: async () => failed() })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.failedNone)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("a reading that is too old says so and points at the Names page", async () => {
    render(<AddAll api={apiWith([], { confirmKinds: async () => { throw new ApiError("not-found"); } })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.notFound, { exact: false })).toBeTruthy();
    expect(screen.getByRole("link", { name: w.openNames }).getAttribute("href")).toBe("/account/names/100.cmp1");
  });

  it("a refusal says only the DM can add names", async () => {
    render(<AddAll api={apiWith([], { confirmKinds: async () => { throw new ApiError("not-allowed"); } })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.refused)).toBeTruthy();
  });

  it("a wait that stops moving for a minute says it is slower than usual", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const still: ConfirmAnswer = { state: "working", stage: "adding", progress: { done: 1, total: 2, at: 10, createdAt: 1, now: 10 } };
    render(<AddAll api={apiWith([still], { confirmAnswer: async () => still })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    await vi.advanceTimersByTimeAsync(61_000);
    expect(screen.queryByText(w.stalled)).toBeTruthy();
  });
});

describe("the switch and the DM", () => {
  it("shows nothing while the switch is off", () => {
    live.on = false;
    const { container } = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    expect(container.textContent).toBe("");
  });

  it("shows nothing where the API has no answer to add from (a player's read)", () => {
    const { container } = render(
      <AddAll api={{} as unknown as AccountApi} campaign="100.cmp1" upload={upload({ place: 5 })} />,
    );
    expect(container.textContent).toBe("");
  });
});

describe("the answers from the server, and what a failure says", () => {
  it("a failed Undo offers Undo again, never the add", async () => {
    const api = apiWith([done({ place: 5 })], {
      confirmAnswer: async () => done({ place: 5 }),
    });
    api.confirmUndo = async () => ({ answer: "u-1" });
    api.confirmAnswer = async (_c, id) => (id === "u-1" ? { state: "failed", stage: "failed", error: "x" } : done({ place: 5 }));
    render(<AddAll api={api} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undoFailed)).toBeTruthy();
    expect(screen.queryByText(w.failedNone)).toBeNull();
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("a look that fails says the bot may still be adding, not that nothing was added", async () => {
    const api = apiWith([], { confirmAnswer: async () => { throw new ApiError("server"); } });
    render(<AddAll api={api} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.lostTouch, { exact: false })).toBeTruthy();
    expect(screen.queryByText(w.failedNone)).toBeNull();
  });

  it("a done add with nothing in it says nothing was added", async () => {
    render(<AddAll api={apiWith([done({})], { confirmAnswer: async () => done({}) })} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.nothingAdded)).toBeTruthy();
  });

  it("a second press before the first is answered sends nothing more", async () => {
    const sent: string[] = [];
    const api = apiWith([], {
      confirmKinds: async (_c, _u, kinds) => {
        sent.push(String(kinds));
        return { answer: "a-1" };
      },
      confirmAnswer: async () => ({ state: "working", stage: "adding" }),
    });
    render(<AddAll api={api} campaign="100.cmp1" upload={upload({ place: 5 })} />);
    const button = screen.getByRole("button", { name: w.addAll(5) });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(sent).toEqual(["all"]);
  });

  it("a done add asks for the waiting counts again, so the page shows what is left", async () => {
    let asked = 0;
    const api = apiWith([done({ place: 5 })], { confirmAnswer: async () => done({ place: 5 }) });
    render(<AddAll api={api} campaign="100.cmp1" upload={upload({ place: 5 })} onChanged={() => (asked += 1)} />);
    fireEvent.click(screen.getByRole("button", { name: w.addAll(5) }));
    expect(await screen.findByText(w.added(5, "5 places"))).toBeTruthy();
    expect(asked).toBe(1);
  });

  it("a kind with one name gets its own button with the singular word", () => {
    const { container } = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ character: 3, place: 1 })} />);
    expect(Array.from(container.querySelectorAll("button")).map((b) => b.textContent)).toEqual([
      w.addAll(4),
      "Add all 3 characters",
      "Add 1 place",
    ]);
  });

  it("a kind with several names keeps the plural", () => {
    const { container } = render(<AddAll api={apiWith([])} campaign="100.cmp1" upload={upload({ place: 2, character: 3 })} />);
    expect(Array.from(container.querySelectorAll("button")).map((b) => b.textContent)).toContain("Add all 2 places");
  });

  it("a kind with no singular word falls back to the key with spaces", () => {
    expect(plainKindSingular("zz_unknown_kind")).toBe("zz unknown kind");
    expect(plainKindSingular("npc")).toBe("NPC");
  });

  it("the done line for one name uses the singular word", async () => {
    render(<AddAll api={apiWith([done({ place: 1 })], { confirmAnswer: async () => done({ place: 1 }) })} campaign="100.cmp1" upload={upload({ place: 1 })} />);
    fireEvent.click(screen.getByRole("button", { name: w.addOne }));
    expect(await screen.findByText("Added 1 name: 1 place.")).toBeTruthy();
  });
});

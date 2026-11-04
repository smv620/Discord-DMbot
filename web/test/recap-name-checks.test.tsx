/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Names to check" on the recap page (#2114 part 3): the DM answers each new name, the answer only
// queues for the bot, and the page asks again until the name leaves the list. It shows only while
// RECAP_NAME_CHECKS_LIVE is true (false today), so these tests switch it on.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type NameAnswerResult } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { workWords as workMovedWords } from "../src/content/work-progress";
import { nameGroupWords as gw } from "../src/content/name-group-words";
import { nameCheckWords as w } from "../src/content/recap-name-checks";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return live.on;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

// The split, group and Undo buttons (#3406): on only in the split describe below.
const split = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/name-split-switch", () => ({
  get NAME_SPLIT_LIVE() {
    return split.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
  split.on = false;
});

const draft = mockRecaps()[0]!;
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

async function open() {
  live.on = true;
  const api = mockApi("table");
  render(<RecapNameChecks api={api} refId={draft.ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const row = (name: string) => screen.getByRole("group", { name }) as HTMLElement;
const slow = { timeout: 8000 };

describe("names to check", () => {
  it("shows nothing while it is switched off", async () => {
    const { container } = render(<RecapNameChecks api={mockApi("table")} refId={draft.ref} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("lists each name with what the recap says about it, and DMbot decides nothing", async () => {
    await open();
    expect(screen.getByText(w.lead)).toBeTruthy();
    for (const name of ["Brannoch", "Veshla", "Garrick"]) expect(row(name)).toBeTruthy();
    expect(within(row("Brannoch")).getByText(/A smith in Eastmarch\./)).toBeTruthy();
  });

  it("says Yes with a kind, then waits, and the name leaves the list", async () => {
    const api = await open();
    const brannoch = row("Brannoch");
    const picker = within(brannoch).getByLabelText(w.kind) as HTMLSelectElement;
    await act(async () => { fireEvent.change(picker, { target: { value: "place" } }); });
    fireEvent.click(within(brannoch).getByRole("button", { name: w.yesWith("Brannoch") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(1));
    expect(calls(api, "answerNameCheck")[0]).toContain('"action":"confirm","kind":"place"');
    expect(await screen.findAllByText(w.waiting)).not.toHaveLength(0);
    await waitFor(() => expect(screen.queryByRole("group", { name: "Brannoch" })).toBeNull(), slow);
    expect(screen.getByRole("group", { name: "Veshla" })).toBeTruthy(); // the others stay
  });

  it("says a name is the same as one already known, spelled another way, not a name, or explains it", async () => {
    const api = await open();
    const veshla = row("Veshla");
    await act(async () => { fireEvent.change(within(veshla).getByLabelText(w.sameLabel), { target: { value: "e".repeat(32) } }); });
    fireEvent.click(within(veshla).getByRole("button", { name: w.sameFor("Veshla") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(1));
    expect(calls(api, "answerNameCheck")[0]).toContain('"action":"same","known":"' + "e".repeat(32));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Veshla" })).toBeNull(), slow);
    const brannoch = row("Brannoch");
    await act(async () => { fireEvent.input(within(brannoch).getByLabelText(w.spellLabel), { target: { value: "Branock" } }); });
    fireEvent.click(within(brannoch).getByRole("button", { name: w.spellFor("Brannoch") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(2));
    expect(calls(api, "answerNameCheck")[1]).toContain('"action":"respell","text":"Branock"');
    await waitFor(() => expect(screen.queryByRole("group", { name: "Brannoch" })).toBeNull(), slow);
    const garrick = row("Garrick");
    await act(async () => { fireEvent.input(within(garrick).getByLabelText(w.explainLabel), { target: { value: "A guard." } }); });
    fireEvent.click(within(garrick).getByRole("button", { name: w.explainFor("Garrick") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(3));
    expect(calls(api, "answerNameCheck")[2]).toContain('"action":"explain","text":"A guard."');
    fireEvent.click(within(garrick).getByRole("button", { name: w.notANameFor("Garrick") }));
  }, 30_000); // (each answer waits for the list to be asked again, about two seconds)

  it("does not pick a kind for the DM: Yes stays off until one is chosen", async () => {
    await open();
    const veshla = row("Veshla");
    const yes = within(veshla).getByRole("button", { name: w.yesWith("Veshla") }) as HTMLButtonElement;
    expect(yes.disabled).toBe(true);
    expect((within(veshla).getByLabelText(w.kind) as HTMLSelectElement).value).toBe("");
    await act(async () => { fireEvent.change(within(veshla).getByLabelText(w.kind), { target: { value: "npc" } }); });
    expect(yes.disabled).toBe(false);
  });

  it("keeps the buttons off until there is something to send", async () => {
    await open();
    const veshla = row("Veshla");
    expect((within(veshla).getByRole("button", { name: w.sameFor("Veshla") }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(veshla).getByRole("button", { name: w.spellFor("Veshla") }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(veshla).getByRole("button", { name: w.explainFor("Veshla") }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says why when the bot could not apply an answer, and leaves the name to try again", async () => {
    const api = await open();
    const garrick = row("Garrick");
    fireEvent.click(within(garrick).getByRole("button", { name: w.notANameFor("Garrick") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toHaveLength(1));
    expect(await within(row("Garrick")).findByRole("alert", undefined, slow)).toBeTruthy();
    expect(within(row("Garrick")).getByText(w.problem.gone as string)).toBeTruthy();
  });

  it("says plainly when an answer is refused", async () => {
    const api = mockApi("table");
    api.answerNameCheck = async () => {
      throw new ApiError("too-soon");
    };
    live.on = true;
    render(<RecapNameChecks api={api} refId={draft.ref} />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(within(row("Veshla")).getByRole("button", { name: w.notANameFor("Veshla") }));
    expect(await within(row("Veshla")).findByText(w.problem["too-soon"] as string)).toBeTruthy();
  });

  it("says it could not load the names, and nothing when there are none", async () => {
    live.on = true;
    const down = mockApi("table");
    down.nameChecks = async () => {
      throw new ApiError("network");
    };
    render(<RecapNameChecks api={down} refId={draft.ref} />);
    expect(await screen.findByText(w.loadFailed)).toBeTruthy();
    cleanup();
    const none = mockApi("table");
    none.nameChecks = async () => ({ names: [], known: [], kinds: [] });
    const { container } = render(<RecapNameChecks api={none} refId={draft.ref} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});

describe("accessibility", () => {
  it("has nothing serious or critical, and every control has a name", async () => {
    await open();
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
});

describe("the real client", () => {
  it("reads the list and posts one answer to the name's own address", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method ?? "GET"} ${new URL(url).pathname} ${init?.body ?? ""}`);
      return new Response(JSON.stringify(init?.method === "POST" ? { answerId: "x" } : { names: [], known: [], kinds: [] }), { status: init?.method === "POST" ? 202 : 200 });
    }) as typeof fetch);
    await api.nameChecks!("5.abc.1700");
    await api.answerNameCheck!("5.abc.1700", "n1", { action: "not_a_name" });
    expect(seen).toEqual([
      "GET /me/recaps/5.abc.1700/names-to-check ",
      'POST /me/recaps/5.abc.1700/names-to-check/n1 {"action":"not_a_name"}',
    ]);
  });
});

describe("the codes the bot writes", () => {
  // The codes come from core, so a new one there fails here until the page has words for it. Not
  // listed: PLAYER_CHARACTER, a kind (who plays a character is asked in Discord), never an error.
  const source = readFileSync(join(process.cwd(), "..", "core", "src", "dmbot", "recaps", "name_answers.py"), "utf8"); // (tests run from web/)
  const codes = [...source.matchAll(/^([A-Z_]+) = "([a-z_]+)"/gm)]
    .filter((m) => m[1] !== "PLAYER_CHARACTER")
    .map((m) => m[2] as string);

  it("are read from core (a guard that the reading works)", () => {
    expect(codes).toEqual(expect.arrayContaining(["not_allowed", "gone", "bad_kind", "not_joined", "bad_spelling", "failed"]));
  });

  it.each([...new Set([...codes, "busy"])])("has plain words for %s", (code) => {
    // The split, group and Undo refusals (#3406) are shown through `nameGroupWords.errors` (splitReason), not `problem`.
    const said = w.problem[code.replaceAll("_", "-")] ?? (Object.hasOwn(gw.errors, code) ? gw.reason(code) : undefined);
    expect(said, code).toBeTruthy();
    expect(said).not.toBe(w.problem.server);
    expect(said).not.toBe(gw.other);
  });
});

describe("a busy answer through the real client", () => {
  it("is read as slow-down, and the page has words for it", async () => {
    const api = httpApi("https://api.example", (async () => new Response(JSON.stringify({ detail: "busy" }), { status: 409 })) as typeof fetch);
    const caught = await api.answerNameCheck!("5.abc.1700", "n1", { action: "not_a_name" }).catch((e) => e);
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.kind).toBe("slow-down");
    expect(w.problem[caught.kind]).toBeTruthy();
    expect(w.problem[caught.kind]).not.toBe(w.problem.server);
  });
});

describe("a name the bot never finishes", () => {
  it("says it is still saving, stops the clock when the page gives up, and keeps the answer", async () => {
    live.on = true;
    vi.useFakeTimers();
    const api = mockApi("table");
    const real = api.nameChecks!.bind(api);
    api.nameChecks = async (ref) => {
      const got = await real(ref);
      return { ...got, names: got.names.map((n) => ({ ...n, answering: true })) };
    };
    render(<RecapNameChecks api={api} refId={draft.ref} />);
    await vi.advanceTimersByTimeAsync(200);
    await vi.advanceTimersByTimeAsync(130_000);
    expect(screen.getByText(w.slow)).toBeTruthy();
    expect(screen.getByText(workMovedWords.stoppedLooking)).toBeTruthy();
    expect(screen.queryByText(/Running for/)).toBeNull();
  });
});

// Split, group and Undo on a list of names (#3406). The bot's word on each answer comes from a stub that
// answers in order: each poll takes the next step, and the last step repeats.
describe("names to check: split, group and Undo (#3406)", () => {
  const LIST = "Aldmoor, Quillgate and Fenwick";
  const PAIR = "Brannoch and Veshla";
  const done7: NameAnswerResult = { state: "done", note: "Split into 3 names.", error: null, batch: 7 };
  const working: NameAnswerResult = { state: "working", note: "", error: null, batch: null };
  const failed = (error: string): NameAnswerResult => ({ state: "failed", note: "", error, batch: null });
  const notFound = new ApiError("not-found");
  // Each poll of the bot's word takes the next step; the last one repeats.
  function script(api: ReturnType<typeof mockApi>, steps: (NameAnswerResult | ApiError)[]) {
    let at = 0;
    api.nameAnswerResult = async () => {
      const step = steps[Math.min(at, steps.length - 1)]!;
      at += 1;
      if (step instanceof ApiError) throw step;
      return step;
    };
  }
  async function openSplit() {
    split.on = true;
    const api = await open();
    return api;
  }

  it("no split buttons when the switch is off", async () => {
    split.on = false;
    await open();
    expect(row(LIST)).toBeTruthy(); // the card is there, so the absence below means something
    expect(screen.queryByRole("button", { name: `${gw.split}: ${LIST}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `${gw.isGroup}: ${LIST}` })).toBeNull();
  });

  it("list card shows both buttons, a one-name card shows neither", async () => {
    await openSplit();
    const list = row(LIST);
    expect(within(list).getByRole("button", { name: `${gw.split}: ${LIST}` })).toBeTruthy();
    expect(within(list).getByRole("button", { name: `${gw.isGroup}: ${LIST}` })).toBeTruthy();
    const one = row("Brannoch");
    expect(within(one).queryByRole("button", { name: `${gw.split}: Brannoch` })).toBeNull();
    expect(within(one).queryByRole("button", { name: `${gw.isGroup}: Brannoch` })).toBeNull();
  });

  it("hint card shows the hint and \"Yes, that group\"", async () => {
    await openSplit();
    const pair = row(PAIR);
    expect(within(pair).getByText(gw.hint("The Ashen Circle", ["Brannoch"]))).toBeTruthy();
    expect(within(pair).getByRole("button", { name: `${gw.yesGroup}: The Ashen Circle` })).toBeTruthy();
  });

  it("split sends the split answer and then offers Undo", async () => {
    const api = await openSplit();
    script(api, [working, done7]);
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    await waitFor(() => expect(calls(api, "answerNameCheck").some((c) => c.includes('"action":"split"'))).toBe(true));
    expect(await screen.findByText("Split into 3 names.", undefined, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: gw.undo })).toBeTruthy();
  }, 30_000);

  it("They're a group asks for the name, sends it trimmed, and Cancel hides the box", async () => {
    const api = await openSplit();
    const list = row(LIST);
    fireEvent.click(within(list).getByRole("button", { name: `${gw.isGroup}: ${LIST}` }));
    const box = within(list).getByLabelText(gw.groupAsk) as HTMLInputElement;
    // Cancel closes the box, with nothing sent.
    await act(async () => {
      fireEvent.input(box, { target: { value: "The Ashen Circle" } });
    });
    fireEvent.click(within(list).getByRole("button", { name: gw.groupCancel }));
    expect(within(list).queryByLabelText(gw.groupAsk)).toBeNull();
    expect(calls(api, "answerNameCheck").some((c) => c.includes('"action":"make_group"'))).toBe(false);
    // Open again: the name is trimmed before it is sent.
    fireEvent.click(within(list).getByRole("button", { name: `${gw.isGroup}: ${LIST}` }));
    const again = within(list).getByLabelText(gw.groupAsk) as HTMLInputElement;
    const make = within(list).getByRole("button", { name: `${gw.groupMake}: ${LIST}` }) as HTMLButtonElement;
    expect(make.disabled).toBe(true);
    await act(async () => {
      fireEvent.input(again, { target: { value: "  The Ashen Circle " } });
    });
    expect(make.disabled).toBe(false);
    fireEvent.click(make);
    await waitFor(() => expect(calls(api, "answerNameCheck").some((c) => c.includes('"action":"make_group"'))).toBe(true));
    expect(calls(api, "answerNameCheck").find((c) => c.includes('"action":"make_group"'))).toContain('"name":"The Ashen Circle"');
  }, 30_000);

  it("Undo waits for the bot, then says Taken back.", async () => {
    const api = await openSplit();
    script(api, [working, done7, done7, notFound]);
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    await screen.findByText("Split into 3 names.", undefined, slow);
    fireEvent.click(screen.getByRole("button", { name: gw.undo }));
    await waitFor(() => expect(calls(api, "answerNameCheck").some((c) => c.includes('"action":"undo","batch":7'))).toBe(true));
    // The bot still has the split, so Undo stays off while it waits.
    await waitFor(() => expect((screen.getByRole("button", { name: gw.undo }) as HTMLButtonElement).disabled).toBe(true), slow);
    expect(await screen.findByText(gw.undone, undefined, slow)).toBeTruthy();
    expect(screen.queryByRole("button", { name: gw.undo })).toBeNull();
  }, 40_000);

  it("a plain answer clears Undo", async () => {
    const api = await openSplit();
    script(api, [working, done7]);
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    await screen.findByText("Split into 3 names.", undefined, slow);
    expect(screen.getByRole("button", { name: gw.undo })).toBeTruthy();
    fireEvent.click(within(row("Garrick")).getByRole("button", { name: w.notANameFor("Garrick") }));
    expect(screen.queryByRole("button", { name: gw.undo })).toBeNull();
  }, 30_000);

  it("a refused Undo shows the reason", async () => {
    const api = await openSplit();
    script(api, [working, done7, failed("answered")]);
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    await screen.findByText("Split into 3 names.", undefined, slow);
    fireEvent.click(screen.getByRole("button", { name: gw.undo }));
    expect(await screen.findByText(gw.reason("answered"), undefined, slow)).toBeTruthy();
  }, 40_000);

  it("the list is read again when a follow ends", async () => {
    const api = await openSplit();
    script(api, [working, done7]);
    const before = calls(api, "nameChecks").length;
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    await screen.findByText("Split into 3 names.", undefined, slow);
    await waitFor(() => expect(calls(api, "nameChecks").length).toBeGreaterThan(before), slow);
  }, 30_000);

  it("the wait shows Working on it… while a split is followed", async () => {
    const api = await openSplit();
    script(api, [working, done7]);
    fireEvent.click(within(row(LIST)).getByRole("button", { name: `${gw.split}: ${LIST}` }));
    expect(await screen.findAllByText(gw.working, undefined, slow)).not.toHaveLength(0);
    await screen.findByText("Split into 3 names.", undefined, slow);
    expect(screen.queryAllByText(gw.working)).toHaveLength(0);
  }, 30_000);
});

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

import { ApiError, httpApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
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

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
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
    expect(await screen.findByText(w.waiting)).toBeTruthy();
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
    const said = w.problem[code.replaceAll("_", "-")];
    expect(said, code).toBeTruthy();
    expect(said).not.toBe(w.problem.server);
  });
});

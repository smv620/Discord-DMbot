/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The kind menu on a name to check starts on DMbot's best guess (#5218): one tap on Yes usually answers it, and choosing the guess
// sends nothing by itself. A person the story never named shows as their description with a quiet "(no name yet)".
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameCheckWords as nw } from "../src/content/recap-name-checks";

vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return true;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const draft = mockRecaps()[0]!;

/** The mock's list, with Veshla's guess and Garrick's name changed as the test says. */
function withGuess(guess: string | null | undefined, rename?: string) {
  const api = mockApi("table");
  const real = api.nameChecks!.bind(api);
  api.nameChecks = async (...a) => {
    const got = await real(...a);
    return {
      ...got,
      names: got.names.map((n) => {
        if (n.id !== "b".repeat(32)) return n;
        const { guess: _unused, ...rest } = n as typeof n & { guess?: unknown };
        return { ...rest, ...(rename ? { name: rename } : {}), ...(guess !== undefined ? { guess } : {}) };
      }),
    };
  };
  return api;
}

async function open(api: ReturnType<typeof mockApi>) {
  render(<RecapNameChecks api={api} refId={draft.ref} />);
  await screen.findByRole("heading", { name: nw.heading });
}
const card = (name: string | RegExp) => within(screen.getByRole("group", { name }));

describe("the kind menu", () => {
  it("starts on DMbot's guess with Yes ready, and sends nothing by itself", async () => {
    const api = withGuess("npc");
    const spy = vi.spyOn(api, "answerNameCheck");
    await open(api);
    const veshla = card("Veshla");
    const select = veshla.getByLabelText(nw.kind) as HTMLSelectElement;
    expect(select.value).toBe("npc");
    const yes = veshla.getByRole("button", { name: nw.yesWith("Veshla") }) as HTMLButtonElement;
    expect(yes.disabled).toBe(false);
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(yes);
    expect(spy).toHaveBeenCalledWith(draft.ref, "b".repeat(32), { action: "confirm", kind: "npc" });
  });

  it("with no guess starts on Pick what it is, and Yes waits for a kind", async () => {
    await open(withGuess(null));
    const veshla = card("Veshla");
    expect((veshla.getByLabelText(nw.kind) as HTMLSelectElement).value).toBe("");
    expect((veshla.getByRole("button", { name: nw.yesWith("Veshla") }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a guess that is not one of the list's kinds is ignored", async () => {
    await open(withGuess("starship"));
    expect((card("Veshla").getByLabelText(nw.kind) as HTMLSelectElement).value).toBe("");
  });

  it("choosing another kind sends that kind", async () => {
    const api = withGuess("npc");
    const spy = vi.spyOn(api, "answerNameCheck");
    await open(api);
    const veshla = card("Veshla");
    fireEvent.change(veshla.getByLabelText(nw.kind), { target: { value: "place" } });
    fireEvent.click(veshla.getByRole("button", { name: nw.yesWith("Veshla") }));
    expect(spy).toHaveBeenCalledWith(draft.ref, "b".repeat(32), { action: "confirm", kind: "place" });
  });
});

describe("a person the story never named", () => {
  it("shows their description with a quiet (no name yet), not 'unnamed - …'", async () => {
    await open(withGuess("npc", "unnamed - Cerric Vanya'elen's paternal grandfather"));
    const group = screen.getByRole("group", { name: /Cerric Vanya'elen's paternal grandfather/ });
    expect(group.textContent).toContain(`Cerric Vanya'elen's paternal grandfather ${nw.noNameYet}`);
    expect(group.textContent).not.toContain("unnamed - ");
  });

  it("leaves an ordinary name alone", async () => {
    await open(withGuess("npc"));
    expect(card("Veshla").queryByText(nw.noNameYet)).toBeNull();
  });
});

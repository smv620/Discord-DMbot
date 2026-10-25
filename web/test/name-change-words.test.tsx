/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Change the words" on a name to check (#2593; core #2570): "Harrow" is often "Harrow's Keep". Shown only
// while NAME_CHANGE_WORDS_LIVE is true (false today), so these tests switch it on.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameCheckWords as w } from "../src/content/recap-name-checks";

const live = vi.hoisted(() => ({ change: false }));
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
    get NAME_CHANGE_WORDS_LIVE() {
      return live.change;
    },
  };
});

afterEach(() => {
  cleanup();
  live.change = false;
});

const draft = mockRecaps()[0]!;
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));
const slow = { timeout: 8000 };

async function open(on = true) {
  live.change = on;
  const api = mockApi("table");
  render(<RecapNameChecks api={api} refId={draft.ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const row = (name: string) => screen.getByRole("group", { name }) as HTMLElement;
const box = (name: string) => within(row(name)).getByLabelText(w.changeLabel) as HTMLInputElement;

describe("change the words", () => {
  it("is not offered while it is switched off", async () => {
    await open(false);
    expect(screen.queryByText(w.changeUse)).toBeNull();
  });

  it("starts as the name and keeps the button off until the words differ", async () => {
    await open();
    expect(box("Brannoch").value).toBe("Brannoch");
    const button = within(row("Brannoch")).getByRole("button", { name: w.changeFor("Brannoch") }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.input(box("Brannoch"), { target: { value: "Brannoch's Forge" } });
    expect(button.disabled).toBe(false);
  });

  it("sends the new words, keeps the card with its new name, and says what to do next", async () => {
    const api = await open();
    fireEvent.input(box("Brannoch"), { target: { value: "  Brannoch's Forge " } });
    fireEvent.click(within(row("Brannoch")).getByRole("button", { name: w.changeFor("Brannoch") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")).toEqual([`answerNameCheck:${draft.ref}:a:${JSON.stringify({ action: "change_words", words: "Brannoch's Forge" })}`]));
    expect((await screen.findAllByText(w.waiting, undefined, slow)).length).toBeGreaterThan(0);
    expect(await screen.findByRole("group", { name: "Brannoch's Forge" }, slow)).toBeTruthy();
    expect(screen.getAllByText(w.changed).length).toBeGreaterThan(0);
    expect(screen.queryByRole("group", { name: "Brannoch" })).toBeNull();
  });

  it("offers up to three longer names, and picking one sends it as the heard form too", async () => {
    const api = await open();
    const pick = within(row("Brannoch")).getByRole("button", { name: "Brannoch's Forge" });
    fireEvent.click(pick);
    expect(box("Brannoch").value).toBe("Brannoch's Forge");
    fireEvent.click(within(row("Brannoch")).getByRole("button", { name: w.changeFor("Brannoch") }));
    await waitFor(() => expect(calls(api, "answerNameCheck")[0]).toContain(JSON.stringify({ action: "change_words", words: "Brannoch's Forge", heard: "Brannoch's Forge" })));
    expect(within(row("Veshla")).queryByRole("group", { name: w.longerLabel })).toBeNull(); // none for this card
  });

  it("says each refusal in plain words and never names the entry a secret name clashes with", async () => {
    await open();
    fireEvent.input(box("Brannoch"), { target: { value: "Zeta" } });
    fireEvent.click(within(row("Brannoch")).getByRole("button", { name: w.changeFor("Brannoch") }));
    const alert = await screen.findByRole("alert", undefined, slow);
    expect(alert.textContent).toBe(w.problem["secret-clash"]);
    expect(w.problem["secret-clash"]).not.toMatch(/Zeta|secret name/i);
    for (const code of ["empty", "too-long", "already-known"]) expect(w.problem[code]).toBeTruthy();
  });

  it("has no accessibility problems with the form open", async () => {
    await open();
    (within(row("Brannoch")).getByText(w.more).closest("details") as HTMLDetailsElement).open = true;
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});

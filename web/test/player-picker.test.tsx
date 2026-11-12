/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The wizard's step 3 picker (#4204): it looks and works like a picker, never drops what was typed, and shows only names. Run against the
// wizard's pretend API (wizard-mock.ts): Zoe played with this DM before; Ada, Ben and Eric the Red did not.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Wizard from "../src/account/Wizard";
import { mockWizard } from "../src/account/wizard-mock";
import { wizardWords as w } from "../src/content/wizard-words";

vi.mock("../src/content/wizard-switch", () => ({
  WIZARD_LIVE: true,
  WIZARD_SHEETS_LIVE: false,
  WIZARD_HOUSE_FILES_LIVE: false,
  WIZARD_OWN_RULESET_LIVE: false,
  WIZARD_DRIVE_LIVE: false,
  WIZARD_LINKS_LIVE: false,
}));

afterEach(cleanup);
const G = "300000000000000001";

/** Step 3 with two characters, Okthandom and Brannoc. */
async function onStep3(guild = G) {
  const api = mockWizard();
  render(<Wizard api={api} guild={guild} />);
  fireEvent.click(await screen.findByRole("button", { name: w.start }));
  await screen.findByText(w.step(1, 10));
  fireEvent.click(screen.getByRole("button", { name: w.next }));
  await screen.findByText(w.step(2, 10));
  for (const name of ["Okthandom", "Brannoc"]) {
    fireEvent.input(screen.getByLabelText(new RegExp(w.confirm.fullLabel)), { target: { value: name } });
    fireEvent.click(screen.getByRole("button", { name: w.confirm.add }));
    await screen.findByText(new RegExp(name));
  }
  fireEvent.click(screen.getByRole("button", { name: w.next }));
  await screen.findByText(w.step(3, 10));
  await screen.findByLabelText("Okthandom");
  return api;
}
const box = (name: string) => screen.getByLabelText(name) as HTMLInputElement;
const savedPlayers = async (api: ReturnType<typeof mockWizard>) => (await api.get(G))?.answers.characters.map((c) => c.player);

describe("the player picker", () => {
  it("shows a placeholder and a down arrow, and the list opens on focus", async () => {
    await onStep3();
    expect(box("Okthandom").placeholder).toBe(w.players.placeholder);
    expect(screen.getAllByRole("button", { name: w.players.open }).length).toBe(2);
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.focus(box("Okthandom"));
    const list = await screen.findByRole("listbox");
    const names = within(list).getAllByRole("option").map((o) => o.textContent);
    // "Not set yet" first, then those who played with this DM before, marked, then the rest by name
    expect(names).toEqual([w.players.pending, `Zoe · ${w.players.before}`, "Ada", "Ben", "Eric the Red"]);
  });

  it("typing filters the list, and Enter picks the first match", async () => {
    const api = await onStep3();
    fireEvent.input(box("Okthandom"), { target: { value: "er" } });
    const list = await screen.findByRole("listbox");
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual([w.players.pending, "Eric the Red"]);
    fireEvent.keyDown(box("Okthandom"), { key: "ArrowDown" });
    fireEvent.keyDown(box("Okthandom"), { key: "Enter" });
    await waitFor(async () => expect(await savedPlayers(api)).toEqual(["1004", "pending"]));
    expect(box("Okthandom").value).toBe("Eric the Red"); // (the name, never the id)
  });

  it("arrow keys move, Escape closes without picking", async () => {
    const api = await onStep3();
    fireEvent.focus(box("Brannoc"));
    await screen.findByRole("listbox");
    fireEvent.keyDown(box("Brannoc"), { key: "ArrowDown" });
    fireEvent.keyDown(box("Brannoc"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(await savedPlayers(api)).toEqual(["pending", "pending"]);
  });

  it("pressing a name picks it, and Not set yet puts it back", async () => {
    const api = await onStep3();
    fireEvent.focus(box("Brannoc"));
    fireEvent.click(await screen.findByRole("option", { name: new RegExp("^Ada") }));
    await waitFor(async () => expect(await savedPlayers(api)).toEqual(["pending", "1002"]));
    fireEvent.focus(box("Brannoc"));
    fireEvent.click(await screen.findByRole("option", { name: w.players.pending }));
    await waitFor(async () => expect(await savedPlayers(api)).toEqual(["pending", "pending"]));
  });

  it("text that matches no one stays in the box with a line, and Next keeps the character not set yet", async () => {
    const api = await onStep3();
    fireEvent.input(box("Okthandom"), { target: { value: "Zed" } });
    fireEvent.blur(box("Okthandom"));
    expect(box("Okthandom").value).toBe("Zed"); // (never silently removed)
    expect(await screen.findByText(w.players.noMatch("Zed"))).toBeTruthy();
    expect(await savedPlayers(api)).toEqual(["pending", "pending"]);
    expect(screen.getByText(w.players.noPlayerYet(2))).toBeTruthy(); // (the summary line)
    fireEvent.click(screen.getByRole("button", { name: w.next }));
    await screen.findByText(w.step(4, 10));
    expect(await savedPlayers(api)).toEqual(["pending", "pending"]);
  });

  it("shows no Discord id anywhere", async () => {
    await onStep3();
    fireEvent.focus(box("Okthandom"));
    await screen.findByRole("listbox");
    expect(document.body.textContent).not.toMatch(/\b1001\b|\b1002\b|\b1003\b|\b1004\b/);
  });

  it("an empty member list says so, and the DM can go on", async () => {
    await onStep3(`${G}.nomembers`);
    expect(await screen.findByText(w.players.cannotSee)).toBeTruthy();
  });

  it("a member list that cannot be read says so too", async () => {
    await onStep3(`${G}.brokenmembers`);
    expect(await screen.findByText(w.players.cannotSee)).toBeTruthy();
  });
});

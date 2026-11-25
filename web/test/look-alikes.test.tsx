/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "🔗 Look-alikes" (#5259): the groups as cards, the same answers as Discord's panel (tick, name to keep, 🔗 Merge with one confirmation,
// None are the same, Undo), and the Questions page's line ("Answer them here" while the switch is on, else Discord's own panel).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import LookAlikes from "../src/account/LookAlikes";
import { mockApi } from "../src/account/mock";
import Questions from "../src/account/Questions";
import { lookAlikeWords as w } from "../src/content/lookalikes-words";
import { kindWords, questionsPage as qw } from "../src/content/questions-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/lookalikes-switch", () => ({
  get LOOKALIKES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
const card = (n: number) => screen.getAllByRole("group").filter((g) => g.tagName === "FIELDSET")[n]!;

async function open(api = mockApi("table")) {
  render(<LookAlikes api={api} campaignRef={REF} />);
  await screen.findByText(w.question(1, 2));
  return api;
}

/** The Undo shows while the merge's last read is still going; it takes a press only once that is done. */
async function pressUndo() {
  const button = (await screen.findByRole("button", { name: w.undo })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
}

describe("the groups", () => {
  it("show each group's names with kind and how often heard, all ticked, the most heard to keep", async () => {
    await open();
    const c = within(card(0));
    expect(c.getByText("Bel-Eros (NPC, heard 7 times)")).toBeTruthy();
    expect(c.getByText("Baleros (place)")).toBeTruthy(); // (heard once: no count)
    for (const box of c.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(true);
    expect((c.getByLabelText(w.keepLabel) as HTMLSelectElement).value).toBe("l1".repeat(16));
    expect(screen.getByRole("heading", { name: w.heading(2) })).toBeTruthy();
  });

  it("say so when only one name is ticked, or the name to keep is unticked", async () => {
    await open();
    const c = within(card(0));
    const boxes = c.getAllByRole("checkbox");
    fireEvent.click(boxes[1]!);
    fireEvent.click(boxes[2]!);
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    expect(await c.findByText(w.oneTicked)).toBeTruthy();
    fireEvent.click(boxes[1]!);
    fireEvent.click(boxes[2]!);
    fireEvent.click(boxes[0]!);
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    expect(await c.findByText(w.keepUnticked)).toBeTruthy();
  });
});

describe("a merge", () => {
  it("asks once, naming every name (and a name of another kind), then says Done with an Undo", async () => {
    const api = await open();
    const c = within(card(0));
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    expect(
      c.getByText(
        "Merge Beleros, Baleros into Bel-Eros? They become other names for Bel-Eros. Connections, notes and mentions go together. Nothing is deleted, and you can undo it. Baleros is a place; it becomes an NPC's other name.",
      ),
    ).toBeTruthy();
    fireEvent.click(c.getByRole("button", { name: w.no }));
    expect(c.queryByText(w.yes)).toBeNull();
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    expect(await screen.findByText(w.done(2, "Bel-Eros"))).toBeTruthy();
    expect(api.calls.some((x) => x.startsWith("mergeLookAlikes") && x.includes('"keep":"' + "l1".repeat(16) + '"'))).toBe(true);
    // the merged group has gone and the count with it
    await waitFor(() => expect(screen.getByRole("heading", { name: w.heading(1) })).toBeTruthy());
    expect(screen.getByText(w.undoHint(2, "Bel-Eros"))).toBeTruthy();
  });

  it("Undo takes it back, and the group comes back", async () => {
    const api = await open();
    const c = within(card(0));
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    await screen.findByText(w.done(2, "Bel-Eros"));
    await pressUndo();
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(api.calls.some((x) => x.startsWith("undoLookAlikes:100.cmp1:21"))).toBe(true);
    await waitFor(() => expect(screen.getByRole("heading", { name: w.heading(2) })).toBeTruthy());
  });

  it("an Undo that is too late says so", async () => {
    const api = await open();
    api.undoLookAlikes = async () => {
      throw new ApiError("undo-late");
    };
    const c = within(card(0));
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    await pressUndo();
    expect(await screen.findByText(w.tooLate)).toBeTruthy();
  });
});

describe("a merge that does not finish as expected", () => {
  it("sends the unticked names as not the same, and adds Discord's extra line when a secret name was joined", async () => {
    const api = await open();
    const real = api.mergeLookAlikes!.bind(api);
    api.mergeLookAlikes = async (ref, body, onLook) => ({ ...(await real(ref, body, onLook)), secret: "A secret name was joined; players can't see it." });
    const c = within(card(0));
    fireEvent.click(c.getAllByRole("checkbox")[2]!); // Baleros unticked
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    expect(await screen.findByText(`${w.done(1, "Bel-Eros")} A secret name was joined; players can't see it.`)).toBeTruthy();
    expect(api.calls.some((x) => x.startsWith("mergeLookAlikes") && x.includes(`"apart":["${"l3".repeat(16)}"]`))).toBe(true);
  });

  it("says it is still working when the bot has not answered yet, never that nothing changed", async () => {
    const api = await open();
    api.mergeLookAlikes = async () => {
      throw new ApiError("still-waiting");
    };
    const c = within(card(0));
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    expect(await screen.findByText(w.stillWaiting)).toBeTruthy();
    expect(screen.queryByText(w.couldNot)).toBeNull();
  });

  it("a group that comes back after Undo starts fresh, with no confirmation open", async () => {
    await open();
    const c = within(card(0));
    fireEvent.click(c.getByRole("button", { name: w.mergeFor("Bel-Eros") }));
    fireEvent.click(c.getByRole("button", { name: w.yes }));
    await screen.findByText(w.done(2, "Bel-Eros"));
    await pressUndo();
    await waitFor(() => expect(screen.getByRole("heading", { name: w.heading(2) })).toBeTruthy());
    expect(screen.queryByRole("button", { name: w.yes })).toBeNull();
  });
});

describe("None are the same", () => {
  it("is remembered and the group leaves", async () => {
    const api = await open();
    fireEvent.click(within(card(0)).getByRole("button", { name: w.apart }));
    expect(await screen.findByText(w.noted)).toBeTruthy();
    expect(api.calls.some((x) => x.startsWith("lookAlikesApart:100.cmp1:"))).toBe(true);
    await waitFor(() => expect(screen.getByRole("heading", { name: w.heading(1) })).toBeTruthy());
  });
});

describe("the Questions page line", () => {
  const counts = () => ({
    total: 2,
    serverNow: 1,
    campaigns: [{ ref: REF, name: "Test", updatedAt: 1, kinds: [{ kind: "lookalikes", count: 2, oldest: 1 }] }],
  });

  it("answers here while the switch is on", async () => {
    const api = mockApi("table");
    api.myQuestions = async () => counts() as never;
    render(<Questions api={api} />);
    expect(await screen.findByRole("button", { name: new RegExp(qw.showHere) })).toBeTruthy();
    expect(kindWords("lookalikes").how).toBe("discord"); // (the words' own default stays Discord until the page can answer)
  });

  it("says where in Discord, with no link, while it is off", async () => {
    live.on = false;
    const api = mockApi("table");
    api.myQuestions = async () => counts() as never;
    render(<Questions api={api} />);
    expect(await screen.findByText("Answer in Discord: ⚙️ Manage DMbot → 📇 Names → 🔗 Look-alikes.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Names page/ })).toBeNull();
  });

  it("the shipped switch is off", async () => {
    const real = await vi.importActual<typeof import("../src/content/lookalikes-switch")>("../src/content/lookalikes-switch");
    expect(real.LOOKALIKES_LIVE).toBe(false);
  });
});

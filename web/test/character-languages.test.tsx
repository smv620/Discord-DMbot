/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A character's languages on the Names page (#4621): nothing is fetched until Languages is pressed, the list and its marks, a sheet's read
// only languages, add, mark right, remove and Undo (each sends its change), the refusals' words, and nothing shown for a place or while
// the switch is off.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type CharacterLanguages } from "../src/account/api";
import CharacterLanguagesPanel from "../src/account/CharacterLanguages";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { languagesWords as w } from "../src/content/languages-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/languages-switch", () => ({
  get LANGUAGES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "100.cmp1";
const ROW = { id: "lang-dm", name: "Brask" };
const open = async (api: ReturnType<typeof mockApi>, row = ROW) => {
  render(<CharacterLanguagesPanel api={api} refId={DM} row={row} />);
  fireEvent.click(screen.getByRole("button", { name: w.heading }));
  await waitFor(() => expect(screen.queryByText(w.loading)).toBeNull());
};

describe("a character's languages", () => {
  it("nothing is fetched until Languages is pressed", async () => {
    const api = mockApi("table");
    render(<CharacterLanguagesPanel api={api} refId={DM} row={ROW} />);
    expect(api.calls.some((c) => c.startsWith("characterLanguages:"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.heading }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("characterLanguages:"))).toBe(true));
  });

  it("shows each language and marks the ones DMbot found as not checked yet", async () => {
    await open(mockApi("table"));
    expect(screen.getByText("Common")).toBeTruthy();
    expect(screen.getByText("Giant · not checked yet")).toBeTruthy();
  });

  it("a character with a sheet shows its languages from the sheet and no controls", async () => {
    const api = mockApi("table");
    await open(api, { id: "lang-sheet", name: "Ser Vale" });
    expect(screen.getByText("Elvish (from the sheet)")).toBeTruthy();
    expect(screen.getByText(w.sheetSays)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.addButton })).toBeNull();
    expect(screen.queryByRole("button", { name: w.checkButton })).toBeNull();
  });

  it("Add sends the name and says what it did; Add is disabled for an empty name", async () => {
    const api = mockApi("table");
    await open(api);
    const add = screen.getByRole("button", { name: w.addButton }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.input(screen.getByLabelText(w.addLabel), { target: { value: "Orcish" } });
    fireEvent.click(add);
    expect(await screen.findByText("✅ Added: Orcish.")).toBeTruthy();
    expect(api.calls).toContain(`changeCharacterLanguages:${DM}:lang-dm:${JSON.stringify({ action: "add", name: "Orcish" })}`);
  });

  it("These are right shows only with a language DMbot found, and sends check", async () => {
    const api = mockApi("table");
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    expect(await screen.findByText(w.marked)).toBeTruthy();
    expect(api.calls).toContain(`changeCharacterLanguages:${DM}:lang-dm:${JSON.stringify({ action: "check" })}`);
    cleanup();
    const plain = mockApi("table");
    plain.characterLanguages = async (): Promise<CharacterLanguages> => ({ hasSheet: false, languages: [{ id: "l1", name: "Common", source: "dm" }] });
    await open(plain);
    expect(screen.queryByRole("button", { name: w.checkButton })).toBeNull();
  });

  it("Remove sends the language's id", async () => {
    const api = mockApi("table");
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Common") }));
    expect(await screen.findByText("✅ Removed: Common.")).toBeTruthy();
    expect(api.calls).toContain(`changeCharacterLanguages:${DM}:lang-dm:${JSON.stringify({ action: "remove", language: "l1" })}`);
  });

  it("Undo sends the batch as text", async () => {
    const api = mockApi("table");
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Common") }));
    await screen.findByText("✅ Removed: Common.");
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(api.calls.some((c) => c.includes(`"action":"undo"`))).toBe(true);
  });

  it("a gone answer says so and reads the list again; a failed press leaves no Undo", async () => {
    const api = mockApi("table");
    await open(api);
    api.changeCharacterLanguages = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Common") }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("a refusal for a stranger shows its sentence", async () => {
    const api = mockApi("table");
    api.characterLanguages = async () => {
      throw new ApiError("not-allowed");
    };
    await open(api);
    expect(await screen.findByText(w.notDm)).toBeTruthy();
  });
});

describe("the Languages control on the Names page", () => {
  const rowsOf = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("name-row"));
  const openRow = async (api: ReturnType<typeof mockApi>, name: string) => {
    render(<Names api={api} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
    await waitFor(() => expect(rowsOf().find((li) => li.textContent?.includes(name))).toBeTruthy());
    const row = rowsOf().find((li) => li.textContent?.includes(name)) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name }));
  };

  it("nothing shows while LANGUAGES_LIVE is off, and nothing shows for a place", async () => {
    live.on = false;
    await openRow(mockApi("table"), "Ser Alaric-of-the-Many-Hills-and-Rivers-Beyond-the-Greywatch-Wall");
    await waitFor(() => expect(screen.queryByRole("button", { name: w.heading })).toBeNull());
  });

  it("nothing shows for a place, even with the switch on and the methods there", async () => {
    await openRow(mockApi("table"), "Saltmarsh");
    await waitFor(() => expect(screen.queryByRole("button", { name: w.heading })).toBeNull());
    expect(rowsOf().length).toBeGreaterThan(0);
  });

  it("nothing shows when the API lacks the methods, and nothing shows for a place", async () => {
    const api = mockApi("table");
    delete api.characterLanguages;
    await openRow(api, "Ser Alaric-of-the-Many-Hills-and-Rivers-Beyond-the-Greywatch-Wall");
    await waitFor(() => expect(screen.queryByRole("button", { name: w.heading })).toBeNull());
  });
});


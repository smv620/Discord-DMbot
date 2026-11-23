/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's culture and who lives here (#4412, the DM's half): the two lines in Discord's words, the pickers, and what each save
// sends. Each save is its own change; "Not sure" clears the layer, and "Not said" sends no share.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NameChange, PlaceWorld, PlaceWorldRow } from "../src/account/api";
import PlaceCulture from "../src/account/PlaceCulture";
import { cultureLine, populationLine } from "../src/content/place-culture-lines";
import { placeWorldWords as w } from "../src/content/place-world-words";

afterEach(cleanup);

const row = (over: Partial<PlaceWorldRow>): PlaceWorldRow => ({
  layer: "culture",
  value: "dwarf",
  words: "",
  group: null,
  groupName: null,
  how: "strong",
  part: "",
  read: false,
  source: "dm",
  shown: false,
  ...over,
});
const data = (rows: PlaceWorldRow[]): PlaceWorld => ({ level: null, form: null, rows, groups: [], cultures: [{ id: "culture-illuskan", name: "Illuskan" }] });

describe("the culture lines", () => {
  it("read in Discord's words: adjectives for a culture, plurals for who lives here, strengths in brackets", () => {
    expect(cultureLine([row({ value: "dwarf", how: "strong" }), row({ value: "elf", how: "once" })])).toBe("🏛️ Culture: dwarven (strongly), elven (once)");
    expect(
      populationLine([row({ layer: "population", value: "halfling", how: "most" }), row({ layer: "population", value: "human", how: "few" })]),
    ).toBe("🏘️ Who lives here: mostly halflings, a few humans");
  });

  it("a named culture shows its name, and a row DMbot read says so", () => {
    expect(cultureLine([row({ cultureId: "culture-illuskan", culture: "Illuskan", value: "culture-illuskan", how: "some", read: true })])).toBe(
      `🏛️ Culture: Illuskan (somewhat)${w.readMark}`,
    );
  });

  it("a population with no share says no share word", () => {
    expect(populationLine([row({ layer: "population", value: "human", how: "" })])).toBe("🏘️ Who lives here: humans");
  });

  it("no rows, no line", () => {
    expect(cultureLine([])).toBeNull();
    expect(populationLine([])).toBeNull();
  });
});

describe("the pickers", () => {
  it("the culture picker lists the named cultures first, then the lineages, then Something else", () => {
    render(<PlaceCulture data={data([])} name="Saltmarsh" busy={false} send={vi.fn(async () => true)} />);
    fireEvent.click(screen.getByRole("button", { name: w.culture.heading }));
    const select = screen.getByLabelText(w.culture.whose) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toEqual([w.culture.notSure, "Illuskan", "dwarven", "elven", "dragonborn", "gnomish", "goliath", "halfling", "human", "orcish", "tiefling", w.culture.something]);
  });

  it("Save sends map-culture with the value and strength; Not sure sends null", async () => {
    const send = vi.fn(async (_c: NameChange, _ok: string, _m: (g: PlaceWorld) => boolean) => true);
    render(<PlaceCulture data={data([row({ value: "elf", how: "once" })])} name="Saltmarsh" busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: w.culture.heading }));
    fireEvent.change(screen.getByLabelText(w.culture.whose), { target: { value: "dwarf" } });
    fireEvent.change(screen.getByLabelText(w.culture.howStrong), { target: { value: "strong" } });
    fireEvent.click(screen.getAllByRole("button", { name: w.save })[0]!);
    expect(send.mock.calls.at(-1)?.[0]).toEqual({ action: "map-culture", value: "dwarf", how: "strong" });
    fireEvent.change(screen.getByLabelText(w.culture.whose), { target: { value: "" } });
    fireEvent.click(screen.getAllByRole("button", { name: w.save })[0]!);
    expect(send.mock.calls.at(-1)?.[0]).toEqual({ action: "map-culture", value: null });
  });

  it("Not said sends no share, and Something else asks for the DM's words", async () => {
    const send = vi.fn(async (_c: NameChange, _ok: string, _m: (g: PlaceWorld) => boolean) => true);
    render(<PlaceCulture data={data([])} name="Saltmarsh" busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: w.culture.heading }));
    fireEvent.change(screen.getByLabelText(w.population.whoLives), { target: { value: "human" } });
    fireEvent.click(screen.getAllByRole("button", { name: w.save })[1]!);
    expect(send.mock.calls.at(-1)?.[0]).toEqual({ action: "map-population", value: "human", how: "" });
    fireEvent.change(screen.getByLabelText(w.population.whoLives), { target: { value: "other" } });
    expect(screen.getByPlaceholderText(w.culture.wordsLabel)).toBeTruthy();
  });
});

describe("what is saved", () => {
  it("the pickers start from the saved rows, and Save is off until something changes", () => {
    const saved = data([row({ value: "dwarf", how: "strong" })]);
    render(<PlaceCulture data={saved} name="Saltmarsh" busy={false} send={vi.fn(async () => true)} />);
    fireEvent.click(screen.getByRole("button", { name: w.culture.heading }));
    expect((screen.getByLabelText(w.culture.whose) as HTMLSelectElement).value).toBe("dwarf");
    expect((screen.getAllByRole("button", { name: w.save })[0] as HTMLButtonElement).disabled).toBe(true);
  });
});

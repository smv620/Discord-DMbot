/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's land, climate and ruler on the Names page (#4287, the DM's half): the card's lines in Discord's words, the choices, one Save,
// "Not sure", the climate rule, the show switch, and what stays hidden while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { LAND, placeWorldWords as w } from "../src/content/place-world-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/place-world-switch", () => ({
  get PLACE_WORLD_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "/account/names/100.cmp1";
const rows = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("name-row"));
const rowOf = (name: string) => rows().find((li) => li.textContent?.includes(name)) as HTMLElement;

async function openPlace(name: string) {
  const api = mockApi("table");
  render(<Names api={api} path={DM} />);
  await screen.findByRole("list", { name: namesPage.listLabel });
  fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
  await waitFor(() => expect(rowOf(name)).toBeTruthy());
  const row = rowOf(name);
  fireEvent.click(within(row).getByRole("button", { name }));
  return { api, row: within(row) };
}
async function pressSave(row: ReturnType<typeof within>) {
  await waitFor(() => expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(row.getByRole("button", { name: w.save }));
}

describe("the choices", () => {
  it("offers the land menu with Discord's labels and examples, and Not sure", async () => {
    const { row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.land.label)) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([w.land.notSure, ...LAND.map((v) => `${w.land.choices[v]![0]}: ${w.land.choices[v]![1]}`)]);
    expect([...select.options].map((o) => o.textContent)).toContain("Ice and snow: Arctic, glaciers, tundra");
  });

  it("choosing saves nothing until Save, and Save sends only what changed", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.land.label)) as HTMLSelectElement;
    for (const step of ["arctic", "forest", "mountain"]) fireEvent.change(select, { target: { value: step } });
    expect(api.calls.filter((c) => c.includes('"map-'))).toHaveLength(0);
    await pressSave(row);
    expect(await row.findByText(w.landLine("mountains", false))).toBeTruthy();
    expect(api.calls.filter((c) => c.includes('"map-terrain"'))).toHaveLength(1);
    expect(api.calls.some((c) => c.includes('"map-climate"') || c.includes('"map-control"'))).toBe(false);
  });

  it("'only part of it' makes the line say partly", async () => {
    const { row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.land.label), { target: { value: "hill" } });
    fireEvent.click(await row.findByLabelText(w.land.part));
    await pressSave(row);
    expect(await row.findByText(w.landLine("hills", true))).toBeTruthy();
  });

  it("a climate, wet or dry, and a ruler with how it holds the place, are three changes with their own lines", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.climate.label), { target: { value: "cold" } });
    fireEvent.change(await row.findByLabelText(w.climate.wetLabel), { target: { value: "dry" } });
    fireEvent.change(row.getByLabelText(w.ruler.label), { target: { value: "a".repeat(32) } });
    fireEvent.click(await row.findByLabelText(w.ruler.hows["claims"]!));
    await pressSave(row);
    expect(await row.findByText(w.climateLine("cold", "dry"))).toBeTruthy();
    expect(await row.findByText(w.rulerLine("Claimed by", "Ten-Towns"))).toBeTruthy();
    expect(api.calls.filter((c) => c.includes('"map-climate"') && c.includes('"how":"dry"'))).toHaveLength(1);
    expect(api.calls.filter((c) => c.includes('"map-control"') && c.includes('"how":"claims"'))).toHaveLength(1);
  });

  it("Not sure takes a value away", async () => {
    const { row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.land.label), { target: { value: "swamp" } });
    await pressSave(row);
    expect(await row.findByText(w.landLine("swamp", false))).toBeTruthy();
    fireEvent.change(row.getByLabelText(w.land.label), { target: { value: "" } });
    await pressSave(row);
    await waitFor(() => expect(row.queryByText(/^🗺️ Land:/)).toBeNull());
  });
});

describe("the rules", () => {
  it("a smaller place has no climate of its own, and says where to set it", async () => {
    const { row } = await openPlace("Brynwater Crossing");
    expect(await row.findByText(w.climate.tooSmall)).toBeTruthy();
    expect(row.queryByLabelText(w.climate.label)).toBeNull();
  });

  it("the show switch needs something to show, then hides and shows", async () => {
    const { row } = await openPlace("Saltmarsh");
    expect(await row.findByText(w.nothingToShow)).toBeTruthy();
    fireEvent.change(row.getByLabelText(w.land.label), { target: { value: "forest" } });
    await pressSave(row);
    fireEvent.click(await row.findByRole("button", { name: w.show }));
    expect(await row.findByRole("button", { name: w.hide })).toBeTruthy();
  });

  it("only a place has it, and nothing shows while the switch is off", async () => {
    live.on = false;
    const { row } = await openPlace("Saltmarsh");
    expect(row.queryByText(w.heading)).toBeNull();
    expect(row.queryByLabelText(w.land.label)).toBeNull();
  });
});

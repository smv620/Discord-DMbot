/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's land, climate and ruler on the Names page (#4287, the DM's half): the card's lines in Discord's words, the choices, one Save,
// "Not sure", the climate rule, the show switch, and what stays hidden while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type PlaceWorld } from "../src/account/api";
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

describe("what the story put in its own words, other groups, and a save that does not go through", () => {
  const rowOf2 = (over: Record<string, unknown>) => ({ layer: "terrain", value: "forest", words: "", group: null, groupName: null, how: "", part: "", read: true, source: "text", shown: false, ...over });
  async function openWith(rows: PlaceWorld["rows"], tweak?: (api: ReturnType<typeof mockApi>) => void) {
    const api = mockApi("table");
    const real = api.placeWorld!.bind(api);
    api.placeWorld = async (ref, id) => ({ ...(await real(ref, id)), rows });
    tweak?.(api);
    render(<Names api={api} path={DM} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
    await waitFor(() => expect(rowOf("Saltmarsh")).toBeTruthy());
    fireEvent.click(within(rowOf("Saltmarsh")).getByRole("button", { name: "Saltmarsh" }));
    return { api, row: within(rowOf("Saltmarsh")) };
  }

  it("a land in the story's own words is shown with its words and not offered as a choice", async () => {
    const { row } = await openWith([rowOf2({ value: "other", words: "salt flats" }) as never]);
    expect(await row.findByText(`${w.landLine("salt flats", false)}${w.readMark}`)).toBeTruthy();
    expect(row.queryByLabelText(w.land.label)).toBeNull();
  });

  it("every group that holds the place is listed, and a row of only a part of it is left out of the choices", async () => {
    const { row } = await openWith([
      rowOf2({ layer: "control", value: "", group: "a".repeat(32), groupName: "Ten-Towns", how: "rules", read: false, source: "dm" }) as never,
      rowOf2({ layer: "control", value: "", group: "b".repeat(32), groupName: "The Arcane Brotherhood", how: "claims", read: false, source: "dm" }) as never,
      rowOf2({ layer: "terrain", value: "desert", part: "the south", read: false, source: "dm" }) as never,
    ]);
    expect(await row.findByText(w.rulerLine("Ruled by", "Ten-Towns"))).toBeTruthy();
    expect(row.getByText(w.rulerLine("Claimed by", "The Arcane Brotherhood"))).toBeTruthy();
    expect((row.getByLabelText(w.land.label) as HTMLSelectElement).value).toBe(""); // (the southern part is not the whole place's land)
  });

  it("when the first change is not read back as saved, it says so, keeps the other choices and sends nothing more", async () => {
    const { api, row } = await openWith([], (a) => {
      const real = a.changeName!.bind(a);
      a.changeName = async (ref, id, change) => {
        if (change.action === "map-terrain") throw new ApiError("still-waiting");
        return real(ref, id, change);
      };
    });
    fireEvent.change(await row.findByLabelText(w.land.label), { target: { value: "forest" } });
    fireEvent.change(row.getByLabelText(w.ruler.label), { target: { value: "a".repeat(32) } });
    await pressSave(row);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("placeWorld:"))).toBe(true));
    await new Promise((go) => setTimeout(go, 50));
    expect(api.calls.some((c) => c.includes('"map-control"'))).toBe(false);
    expect((row.getByLabelText(w.ruler.label) as HTMLSelectElement).value).toBe("a".repeat(32)); // (the ruler choice is still there)
    expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("what the table may be told (#4311), for a reader who is not a DM", () => {
  const MANAGER = "/account/names/100.cmp1.names-manager";
  async function asReader(setUp?: (api: ReturnType<typeof mockApi>) => Promise<void>) {
    const api = mockApi("table");
    const named = (await api.allNames!("100.cmp1", { q: "Saltmarsh", kind: "", sort: "name", page: 1, size: 20 })).names.find((n) => n.name === "Saltmarsh");
    await setUp?.(api);
    render(<Names api={api} path={MANAGER} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
    await waitFor(() => expect(rowOf("Saltmarsh")).toBeTruthy());
    return { api, row: within(rowOf("Saltmarsh")), id: named!.id };
  }

  it("shows only on request, in Discord's lines, with nothing about who read it", async () => {
    const { api, row, id } = await asReader();
    await api.changeName!("100.cmp1", id, { action: "map-terrain", value: "mountain", how: "" });
    await api.changeName!("100.cmp1", id, { action: "map-climate", value: "cold", how: "dry" });
    await api.changeName!("100.cmp1", id, { action: "map-control", group: "a".repeat(32), how: "rules" });
    await api.changeName!("100.cmp1", id, { action: "map-show", shown: true });
    expect(api.calls.some((c) => c.startsWith("placeWorldTable:"))).toBe(false); // (nothing is fetched until it is pressed)
    fireEvent.click(await row.findByRole("button", { name: w.table.show }));
    expect(await row.findByText(w.landLine("mountains", false))).toBeTruthy();
    expect(row.getByText(w.climateLine("cold", "dry"))).toBeTruthy();
    expect(row.getByText(w.rulerLine("Ruled by", "Ten-Towns"))).toBeTruthy();
    expect(row.queryByText(/DMbot read this/)).toBeNull();
  });

  it("says so when the table knows nothing yet, and nothing the DM has not shown appears", async () => {
    const { api, row, id } = await asReader();
    await api.changeName!("100.cmp1", id, { action: "map-terrain", value: "forest", how: "" }); // (set, never shown)
    fireEvent.click(await row.findByRole("button", { name: w.table.show }));
    expect(await row.findByText(w.table.none)).toBeTruthy();
    expect(row.queryByText(/forest/)).toBeNull();
  });

  it("a DM gets the editor, not the reader's button, and nothing shows while the switch is off", async () => {
    live.on = false;
    const { row } = await asReader();
    expect(row.queryByRole("button", { name: w.table.show })).toBeNull();
  });
});

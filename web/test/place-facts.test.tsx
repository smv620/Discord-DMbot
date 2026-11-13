/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's size, kind and setting on the Names page (#4187, the website half of #4173): the size line, the choice with an example under
// each size, "Not sure", land / water / a road, and "Make it the setting", in the words Discord's place card uses.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { placeWords as w, SIZES } from "../src/content/place-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/place-switch", () => ({
  get PLACE_FACTS_LIVE() {
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

async function openPlace(name: string, path = DM) {
  const api = mockApi("table");
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: namesPage.listLabel });
  fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
  await waitFor(() => expect(rowOf(name)).toBeTruthy());
  const row = rowOf(name);
  fireEvent.click(within(row).getByRole("button", { name }));
  return { api, row: within(row) };
}

describe("a place's size on the Names page", () => {
  it("says the size DMbot read, with the line that it may be wrong", async () => {
    const { row } = await openPlace("Saltmarsh");
    expect(await row.findByText(`${w.sizeLine("settlement")} ${w.readNote}`)).toBeTruthy();
    expect(w.sizeLine("settlement")).toBe("Size: a town or city");
  });

  it("offers each size with its example under it, then Not sure, and the why line", async () => {
    const { row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.sizeAsk)) as HTMLSelectElement;
    const options = [...select.options].map((o) => o.textContent);
    expect(options).toEqual([`${w.notSure[0]}: ${w.notSure[1]}`, ...SIZES.map((s) => `${w.sizes[s][0]}: ${w.sizes[s][1]}`)]);
    expect(options).toContain("Plane: Like the Feywild");
    expect(row.getByText(w.why)).toBeTruthy();
  });

  it("a pick is queued as one change and says Noted, and the line then reads as the DM's", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeAsk), { target: { value: "region" } });
    expect(await row.findByText(w.noted("Saltmarsh", "region"))).toBeTruthy();
    await waitFor(() => expect(row.getByText(w.sizeLine("region"))).toBeTruthy()); // (no "DMbot read this" now)
    expect(api.calls.filter((c) => c.includes('"place-size"'))).toHaveLength(1);
  });

  it("Not sure clears it, and says so", async () => {
    const { row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeAsk), { target: { value: "" } });
    expect(await row.findByText(w.notedNone("Saltmarsh"))).toBeTruthy();
    await waitFor(() => expect(row.queryByText(/^Size:/)).toBeNull());
  });
});

describe("a place's kind and setting", () => {
  it("land, water or a road, with Discord's words, and a line once it is not land", async () => {
    const { row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.formAsk)) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Land or a building: A town, a forest, an inn", "Water: A lake, sea, bay or river", "A road or route: A named road, trail or pass"]);
    fireEvent.change(select, { target: { value: "water" } });
    expect(await row.findByText(w.notedForm("Saltmarsh", "water"))).toBeTruthy();
    await waitFor(() => expect(row.getByText(w.formLine("water"))).toBeTruthy());
  });

  it("the setting button needs a region or bigger, then makes it the setting and can remove it", async () => {
    const { row } = await openPlace("Saltmarsh");
    expect(await row.findByText(w.tooSmall)).toBeTruthy(); // (a town is smaller than a region)
    expect(row.queryByRole("button", { name: w.settingMake })).toBeNull();
    fireEvent.change(row.getByLabelText(w.sizeAsk), { target: { value: "region" } });
    fireEvent.click(await row.findByRole("button", { name: w.settingMake }));
    expect(await row.findByText(w.notedSetting("Saltmarsh"))).toBeTruthy();
    expect(await row.findByText(w.settingLine)).toBeTruthy();
    fireEvent.click(await row.findByRole("button", { name: w.settingRemove }));
    expect(await row.findByText(w.notedNoSetting)).toBeTruthy();
  });
});

describe("who sees it and when", () => {
  it("only a place has the controls", async () => {
    const api = mockApi("table");
    render(<Names api={api} path={DM} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    const person = rows().find((li) => li.classList.contains("name-row") && !li.textContent?.includes("Saltmarsh"))!;
    fireEvent.click(within(person).getAllByRole("button")[0]!);
    expect(within(person).queryByLabelText(w.sizeAsk)).toBeNull();
  });

  it("nothing shows while the switch is off", async () => {
    live.on = false;
    const { row } = await openPlace("Saltmarsh");
    expect(row.queryByLabelText(w.sizeAsk)).toBeNull();
    expect(row.queryByText(/^Size:/)).toBeNull();
  });
});

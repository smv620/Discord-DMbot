/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's size, kind and setting on the Names page (#4187, the website half of #4173): the size line, the choice with an example under
// each size, "Not sure", land / water / a road, and "Make it the setting", in the words Discord's place card uses.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
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
const MANAGER = "/account/names/100.cmp1.names-manager"; // a server manager who is not a DM (names.test.tsx)
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

// (a press on a disabled button is ignored, so wait until the choice has switched Save on)
async function pressSave(row: ReturnType<typeof within>) {
  await waitFor(() => expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(row.getByRole("button", { name: w.save }));
}

describe("a place's size on the Names page", () => {
  it("says the size DMbot read, with the line that it may be wrong", async () => {
    const { row } = await openPlace("Saltmarsh");
    expect(await row.findByText(`${w.sizeLine("settlement")} ${w.readNote}`)).toBeTruthy();
    expect(w.sizeLine("settlement")).toBe("Size: a town or city");
  });

  it("offers each size with its example under it, then Not sure, and the why line", async () => {
    const { row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.sizeLabel)) as HTMLSelectElement;
    const options = [...select.options].map((o) => o.textContent);
    expect(options).toEqual([`${w.notSure[0]}: ${w.notSure[1]}`, ...SIZES.map((s) => `${w.sizes[s][0]}: ${w.sizes[s][1]}`)]);
    expect(options).toContain("Plane: Like the Feywild");
    expect(row.getByText(w.why)).toBeTruthy();
  });

  it("a pick is queued as one change and says Noted, and the line then reads as the DM's", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "region" } });
    await pressSave(row);
    expect(await row.findByText(w.noted("Saltmarsh", "region"))).toBeTruthy();
    await waitFor(() => expect(row.getByText(w.sizeLine("region"))).toBeTruthy()); // (no "DMbot read this" now)
    expect(api.calls.filter((c) => c.includes('"place-size"'))).toHaveLength(1);
  });

  it("Not sure clears it, and says so", async () => {
    const { row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "" } });
    await pressSave(row);
    expect(await row.findByText(w.notedNone("Saltmarsh"))).toBeTruthy();
    await waitFor(() => expect(row.queryByText(/^Size:/)).toBeNull());
  });
});

describe("choosing, then one Save", () => {
  it("moving through the lists saves nothing until Save, and Save sends only what changed", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    const size = (await row.findByLabelText(w.sizeLabel)) as HTMLSelectElement;
    const save = row.getByRole("button", { name: w.save }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // (nothing chosen yet)
    for (const step of ["plane", "world", "region", "district"]) fireEvent.change(size, { target: { value: step } }); // (arrow keys)
    fireEvent.change(row.getByLabelText(w.formAsk), { target: { value: "route" } });
    expect(api.calls.filter((c) => c.includes('"place-'))).toHaveLength(0);
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    expect(await row.findByText(w.notedForm("Saltmarsh", "route"))).toBeTruthy();
    expect(api.calls.filter((c) => c.includes('"place-size"'))).toHaveLength(1); // (the last pick only)
    expect(api.calls.filter((c) => c.includes('"place-form"'))).toHaveLength(1);
  });

  it("choosing the size it already has leaves Save off", async () => {
    const { row } = await openPlace("Saltmarsh");
    const size = (await row.findByLabelText(w.sizeLabel)) as HTMLSelectElement;
    fireEvent.change(size, { target: { value: "region" } });
    fireEvent.change(size, { target: { value: "settlement" } }); // (back to what DMbot read)
    expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("a save that does not go through", () => {
  it("a size that is still waiting or has gone stops the kind from being sent, and a second press waits for the first", async () => {
    const api = mockApi("table");
    render(<Names api={api} path={DM} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
    await waitFor(() => expect(rowOf("Saltmarsh")).toBeTruthy());
    const row = within(rowOf("Saltmarsh"));
    fireEvent.click(row.getByRole("button", { name: "Saltmarsh" }));
    const real = api.changeName!.bind(api);
    let tried = 0;
    api.changeName = async (ref, id, change) => {
      if (change.action === "place-size") {
        tried++;
        throw new ApiError("still-waiting"); // (the bot has not answered)
      }
      return real(ref, id, change);
    };
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "region" } });
    fireEvent.change(row.getByLabelText(w.formAsk), { target: { value: "water" } });
    await pressSave(row);
    await waitFor(() => expect(tried).toBe(1));
    await new Promise((go) => setTimeout(go, 50));
    expect(api.calls.some((c) => c.includes('"place-form"'))).toBe(false); // the kind waits
    // Both choices are still there to save again, and the controls are free again (nothing is stuck busy).
    expect((row.getByLabelText(w.sizeLabel) as HTMLSelectElement).value).toBe("region");
    expect((row.getByLabelText(w.formAsk) as HTMLSelectElement).value).toBe("water");
    expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a kind alone sends no size", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.formAsk), { target: { value: "route" } });
    await pressSave(row);
    expect(await row.findByText(w.notedForm("Saltmarsh", "route"))).toBeTruthy();
    expect(api.calls.some((c) => c.includes('"place-size"'))).toBe(false);
  });

  it("shows that it is saving while it works", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    const real = api.changeName!.bind(api);
    api.changeName = async (ref, id, change) => {
      await new Promise((go) => setTimeout(go, 60));
      return real(ref, id, change);
    };
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "district" } });
    await pressSave(row);
    expect(await row.findByText(w.working)).toBeTruthy();
    expect(await row.findByText(w.noted("Saltmarsh", "district"))).toBeTruthy();
  });
});

describe("a place's kind and setting", () => {
  it("land, water or a road, with Discord's words, and a line once it is not land", async () => {
    const { row } = await openPlace("Saltmarsh");
    const select = (await row.findByLabelText(w.formAsk)) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Land or a building: A town, a forest, an inn", "Water: A lake, sea, bay or river", "A road or route: A named road, trail or pass"]);
    fireEvent.change(select, { target: { value: "water" } });
    await pressSave(row);
    expect(await row.findByText(w.notedForm("Saltmarsh", "water"))).toBeTruthy();
    await waitFor(() => expect(row.getByText(w.formLine("water"))).toBeTruthy());
  });

  it("the setting button needs a region or bigger, then makes it the setting and can remove it", async () => {
    const { row } = await openPlace("Saltmarsh");
    expect(await row.findByText(w.tooSmall)).toBeTruthy(); // (a town is smaller than a region)
    expect(row.queryByRole("button", { name: w.settingMake })).toBeNull();
    fireEvent.change(row.getByLabelText(w.sizeLabel), { target: { value: "region" } });
    await pressSave(row);
    expect(await row.findByText(w.noted("Saltmarsh", "region"))).toBeTruthy();
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
    expect(within(person).queryByLabelText(w.sizeLabel)).toBeNull();
  });

  it("nothing shows while the switch is off", async () => {
    live.on = false;
    const { row } = await openPlace("Saltmarsh");
    expect(row.queryByLabelText(w.sizeLabel)).toBeNull();
    expect(row.queryByText(/^Size:/)).toBeNull();
  });
});

describe("a refusal, a smaller size, a double press, and a non-DM", () => {
  it("a refused setting shows the refusal in a warning", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "region" } });
    await pressSave(row);
    expect(await row.findByText(w.noted("Saltmarsh", "region"))).toBeTruthy();
    const real = api.changeName!.bind(api);
    api.changeName = async (ref, id, change) => {
      if (change.action === "place-setting") throw new ApiError("bad-target");
      return real(ref, id, change);
    };
    fireEvent.click(await row.findByRole("button", { name: w.settingMake }));
    // (the row has other status lines; the refusal is the one warning on it)
    await waitFor(() => expect(row.getAllByRole("status").filter((s) => s.classList.contains("warn"))).toHaveLength(1));
    const [warning] = row.getAllByRole("status").filter((s) => s.classList.contains("warn"));
    expect(warning!.textContent).toBe(namesPage.errors["bad-target"]);
  });

  it("Remove as setting is still offered when the size was made smaller", async () => {
    const { row } = await openPlace("Saltmarsh");
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "region" } });
    await pressSave(row);
    expect(await row.findByText(w.noted("Saltmarsh", "region"))).toBeTruthy();
    fireEvent.click(await row.findByRole("button", { name: w.settingMake }));
    expect(await row.findByText(w.notedSetting("Saltmarsh"))).toBeTruthy();
    fireEvent.change(row.getByLabelText(w.sizeLabel), { target: { value: "building" } });
    await pressSave(row);
    expect(await row.findByText(w.noted("Saltmarsh", "building"))).toBeTruthy();
    expect(await row.findByRole("button", { name: w.settingRemove })).toBeTruthy();
  });

  it("a second press while it is saving sends nothing more", async () => {
    const { api, row } = await openPlace("Saltmarsh");
    const real = api.changeName!.bind(api);
    api.changeName = async (ref, id, change) => {
      await new Promise((go) => setTimeout(go, 60));
      return real(ref, id, change);
    };
    fireEvent.change(await row.findByLabelText(w.sizeLabel), { target: { value: "district" } });
    // Two clicks on the same button before the page re-renders: the second one must be stopped by the in-flight guard, not by
    // the disabled attribute (a disabled button ignores clicks, so a test that waits between presses cannot tell them apart).
    const save = row.getByRole("button", { name: w.save }) as HTMLButtonElement;
    await waitFor(() => expect(save.disabled).toBe(false));
    save.click();
    save.click();
    expect(await row.findByText(w.noted("Saltmarsh", "district"))).toBeTruthy();
    expect(api.calls.filter((c) => c.includes('"place-size"'))).toHaveLength(1);
  });

  it("a reader who is not a DM gets no panel", async () => {
    const api = mockApi("table");
    render(<Names api={api} path={MANAGER} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    // A manager gets the list read-only: a place's name is not a button, so the panel cannot open. Check the row itself.
    await waitFor(() => expect(rowOf("Saltmarsh")).toBeTruthy());
    expect(within(rowOf("Saltmarsh")).queryByRole("button", { name: "Saltmarsh" })).toBeNull();
    expect(screen.queryByLabelText(w.sizeLabel)).toBeNull();
    expect(screen.queryByRole("button", { name: w.save })).toBeNull();
  });
});

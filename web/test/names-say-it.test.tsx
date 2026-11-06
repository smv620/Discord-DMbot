/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "How to say it" in the Names page's row editor (#2317): the box with its saved words, 💡 Suggest
// filling it unsaved, save sending the main name's id, an empty box clearing it, a refused save
// saying so, and nothing at all for a manager or an API that doesn't send the id.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as w } from "../src/content/names-words";
import { sayIt } from "../src/content/say-it-words";

afterEach(cleanup);

const DM = "/account/names/100.cmp1";
const MANAGER = "/account/names/100.cmp1.names-manager";

const rowOf = (name: string) =>
  screen
    .getAllByRole("listitem")
    .find((li) => li.classList.contains("name-row") && li.textContent?.includes(name)) as HTMLElement;
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));

async function edit(path = DM, api = mockApi("table")) {
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: w.listLabel });
  fireEvent.click(within(rowOf("Gorrak")).getByRole("button", { name: "Gorrak" }));
  return { api, row: within(rowOf("Gorrak")) };
}

describe("how to say a name, on the Names page", () => {
  it("shows the box for a DM, empty at first, with the help line", async () => {
    const { row } = await edit();
    const box = row.getByLabelText(w.sayLabel("Gorrak")) as HTMLInputElement;
    expect(box.value).toBe("");
    expect(row.getByText(w.sayHint)).toBeTruthy();
  });

  it("💡 Suggest fills the box and nothing is saved until the DM saves", async () => {
    const { api, row } = await edit();
    fireEvent.click(row.getByRole("button", { name: sayIt.suggest }));
    const box = row.getByLabelText(w.sayLabel("Gorrak")) as HTMLInputElement;
    await waitFor(() => expect(box.value).not.toBe(""));
    expect(calls(api, "suggestSaidAs:100.cmp1:al-n7")).toHaveLength(1);
    expect(calls(api, "saveSaidAs")).toHaveLength(0);
    // The words can be changed before saving.
    fireEvent.input(box, { target: { value: "Gor-rak" } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    await waitFor(() => expect(calls(api, "saveSaidAs").pop()).toBe("saveSaidAs:100.cmp1:al-n7:Gor-rak"));
    expect(await row.findByText(w.saySaved)).toBeTruthy();
  });

  it("the button waits for a change, and again once the words are saved", async () => {
    const { row } = await edit();
    const box = row.getByLabelText(w.sayLabel("Gorrak")) as HTMLInputElement;
    const save = row.getByRole("button", { name: w.saySave }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    fireEvent.input(box, { target: { value: "Gor-rak" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(row.getByText(w.saySaved)).toBeTruthy());
    expect((row.getByRole("button", { name: w.saySave }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("an empty box clears how it is said", async () => {
    const { api, row } = await edit();
    const box = row.getByLabelText(w.sayLabel("Gorrak")) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "Gor-rak" } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    await waitFor(() => expect(row.getByText(w.saySaved)).toBeTruthy());
    fireEvent.input(box, { target: { value: "  " } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    await waitFor(() => expect(calls(api, "saveSaidAs").pop()).toBe("saveSaidAs:100.cmp1:al-n7:"));
  });

  it("says so when the save is refused, and when it can't be reached", async () => {
    const api = mockApi("table");
    api.saveSaidAs = async () => {
      throw new ApiError("bad-text");
    };
    const { row } = await edit(DM, api);
    fireEvent.input(row.getByLabelText(w.sayLabel("Gorrak")), { target: { value: "x" } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    expect(await row.findByText(w.sayTooLong)).toBeTruthy();
    api.saveSaidAs = async () => {
      throw new ApiError("network");
    };
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    expect(await row.findByText(w.errors.network!)).toBeTruthy();
  });

  it("a manager who is not a DM has no box and no way to change anything", async () => {
    render(<Names api={mockApi("table")} path={MANAGER} />);
    await screen.findByRole("list", { name: w.listLabel });
    expect(screen.queryByLabelText(w.sayLabel("Gorrak"))).toBeNull();
    expect(document.querySelectorAll("button.name-open")).toHaveLength(0);
  });

  it("an API that sends no id for the row shows no box", async () => {
    const api = mockApi("table");
    const real = api.allNames!;
    api.allNames = async (ref, q) => {
      const got = await real(ref, q);
      return { ...got, names: got.names.map(({ aliasId: _a, saidAs: _s, ...rest }) => rest) };
    };
    const { row } = await edit(DM, api);
    expect(row.queryByLabelText(w.sayLabel("Gorrak"))).toBeNull();
  });
});

describe("the saved words on the next read", () => {
  it("are in the box when the page is opened again", async () => {
    const api = mockApi("table");
    const first = await edit(DM, api);
    fireEvent.input(first.row.getByLabelText(w.sayLabel("Gorrak")), { target: { value: "Gor-rak" } });
    fireEvent.click(first.row.getByRole("button", { name: w.saySave }));
    await waitFor(() => expect(first.row.getByText(w.saySaved)).toBeTruthy());
    cleanup();
    const again = await edit(DM, api);
    expect((again.row.getByLabelText(w.sayLabel("Gorrak")) as HTMLInputElement).value).toBe("Gor-rak");
  });

  it("an emptied box says it is said as spelled", async () => {
    const { row } = await edit();
    fireEvent.input(row.getByLabelText(w.sayLabel("Gorrak")), { target: { value: "Gor-rak" } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    await waitFor(() => expect(row.getByText(w.saySaved)).toBeTruthy());
    fireEvent.input(row.getByLabelText(w.sayLabel("Gorrak")), { target: { value: "" } });
    fireEvent.click(row.getByRole("button", { name: w.saySave }));
    expect(await row.findByText(w.sayCleared)).toBeTruthy();
  });
});

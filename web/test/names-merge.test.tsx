/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Tick names and "Merge selected" on the All names page (#2427): who sees the tick boxes, the bar
// that waits for two, which name to keep (the most-mentioned first), the different-kinds warning,
// the one confirmation that names every name, what is sent, and what the page says afterwards.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage as w } from "../src/content/names-words";

afterEach(cleanup);

const DM = "/account/names/100.cmp1";
const MANAGER = "/account/names/100.cmp1.names-manager";

function open(path = DM, api = mockApi("table")) {
  render(<Names api={api} path={path} />);
  return api;
}
const ready = async () => screen.findByRole("list", { name: w.listLabel });
const tick = (name: string) => fireEvent.click(screen.getByLabelText(w.pickLabel(name)));
const merges = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.includes("merge-many"));

describe("the tick boxes", () => {
  it("are for the campaign's DMs only", async () => {
    open(MANAGER);
    await ready();
    // (the type filter's boxes are not these: they are in the Type group)
    expect(document.querySelectorAll(".name-pick")).toHaveLength(0);
    cleanup();
    open();
    await ready();
    expect(document.querySelectorAll(".name-pick").length).toBeGreaterThan(1);
  });

  it("show the bar only with two or more ticked, and Clear takes them back", async () => {
    open();
    await ready();
    tick("Mira Voss");
    expect(screen.getByText(w.selectedOne)).toBeTruthy(); // one ticked: told what to do, no merge yet
    expect(screen.queryByRole("button", { name: w.mergeSelected })).toBeNull();
    tick("Brother Anselm");
    expect(screen.getByText(w.selected(2))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.clearSelected }));
    expect(screen.queryByRole("button", { name: w.mergeSelected })).toBeNull();
    expect((screen.getByLabelText(w.pickLabel("Mira Voss")) as HTMLInputElement).checked).toBe(false);
  });
});

describe("what stays ticked", () => {
  it("survives a search, so look-alikes from different searches can be merged together", async () => {
    open();
    await ready();
    tick("Mira Voss");
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "Aldric" } });
    await waitFor(() => expect(screen.queryByLabelText(w.pickLabel("Mira Voss"))).toBeNull());
    tick("Aldric Thorn");
    expect(screen.getByText(w.selected(2))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    expect(await screen.findByRole("radio", { name: /Mira Voss/ })).toBeTruthy();
  });

  it("drops a ticked name that is removed meanwhile, so no ghost is counted or merged", async () => {
    const api = open();
    await ready();
    tick("Mira Voss");
    tick("Aldric Thorn");
    tick("Brother Anselm");
    expect(screen.getByText(w.selected(3))).toBeTruthy();
    const row = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("Mira Voss")) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: w.changeFor("Mira Voss") }));
    fireEvent.click(within(row).getByRole("button", { name: /Remove this name/ }));
    fireEvent.click(await within(row).findByRole("button", { name: /Yes, remove it/ }));
    expect(await screen.findByText(w.selected(2))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    fireEvent.click(await screen.findByRole("button", { name: w.mergeManyYes }));
    await screen.findByText(w.mergedMany("Brother Anselm"));
    expect(merges(api)[0]).not.toContain('"n1"'); // Mira Voss is not in what was sent
  });

  it("shows a renamed ticked name by its new spelling", async () => {
    open();
    await ready();
    tick("Mira Voss");
    tick("Brother Anselm");
    const row = screen.getAllByRole("listitem").find((li) => li.textContent?.includes("Mira Voss")) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: w.changeFor("Mira Voss") }));
    fireEvent.input(within(row).getByLabelText(w.spellLabel), { target: { value: "Mira Vosk" } });
    fireEvent.click(within(row).getByRole("button", { name: w.saveSpelling }));
    await screen.findByText(w.saved);
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    expect(await screen.findByRole("radio", { name: /Mira Vosk/ })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /Mira Voss/ })).toBeNull();
  });
});

describe("merging the ticked names", () => {
  async function ask(...names: string[]) {
    const api = open();
    await ready();
    for (const n of names) tick(n);
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    await screen.findByRole("heading", { name: w.mergeManyHeading });
    return api;
  }

  it("offers the most-mentioned name first and ticked, and one question naming them all", async () => {
    await ask("Mira Voss", "Aldric Thorn", "Brother Anselm");
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((r) => r.closest("label")?.textContent?.split(" · ")[0]?.trim())).toEqual([
      "Brother Anselm",
      "Mira Voss",
      "Aldric Thorn",
    ]);
    expect(radios[0]!.checked).toBe(true);
    expect(screen.getByText(w.mergeManyAsk("Mira Voss and Aldric Thorn", "Brother Anselm"))).toBeTruthy();
  });

  it("says the most-mentioned is first only when the API sent mentions", async () => {
    await ask("Mira Voss", "Brother Anselm");
    expect(screen.getByText(w.mergeManyOrder)).toBeTruthy();
    cleanup();
    const api = mockApi("table");
    const list = api.allNames!.bind(api);
    api.allNames = async (ref, q) => {
      const page = await list(ref, q);
      return { ...page, names: page.names.map(({ mentions: _gone, ...row }) => row) };
    };
    open(DM, api);
    await ready();
    tick("Mira Voss");
    tick("Brother Anselm");
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    await screen.findByRole("heading", { name: w.mergeManyHeading });
    expect(screen.queryByText(w.mergeManyOrder)).toBeNull();
  });

  it("sends merge-many for the kept name, waits, and says it with where Undo is", async () => {
    const api = await ask("Mira Voss", "Brother Anselm");
    fireEvent.click(screen.getByRole("button", { name: w.mergeManyYes }));
    expect(await screen.findByText(w.mergedMany("Brother Anselm"))).toBeTruthy();
    expect(merges(api)).toEqual([`changeName:100.cmp1:n14:${JSON.stringify({ action: "merge-many", others: ["n1"] })}`]);
    // The list is read again: the merged name is off it, and nothing stays ticked.
    await waitFor(() => expect(screen.queryByLabelText(w.pickLabel("Mira Voss"))).toBeNull());
    expect(screen.queryByRole("button", { name: w.mergeSelected })).toBeNull();
    await waitFor(() => expect(document.activeElement?.textContent).toBe(w.mergedMany("Brother Anselm")));
  });

  it("keeps the name the DM picks instead", async () => {
    const api = await ask("Mira Voss", "Brother Anselm");
    fireEvent.click(screen.getByRole("radio", { name: /Mira Voss/ }));
    fireEvent.click(screen.getByRole("button", { name: w.mergeManyYes }));
    await screen.findByText(w.mergedMany("Mira Voss"));
    expect(merges(api)[0]).toContain(`:n1:{"action":"merge-many","others":["n14"]}`);
  });

  it("warns when the kinds differ, and not when they don't", async () => {
    await ask("Mira Voss", "Brynwater Crossing");
    const note = screen.getByRole("note");
    expect(note.textContent).toBe(w.mergeManyKinds("NPCs and Places", "NPCs"));
    cleanup();
    await ask("Mira Voss", "Brother Anselm");
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("is cancelled by Escape or Cancel, sends nothing, and gives the keyboard back", async () => {
    const api = await ask("Mira Voss", "Brother Anselm");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: w.mergeSelected })));
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    fireEvent.click(await screen.findByRole("button", { name: w.mergeManyNo }));
    expect(merges(api)).toHaveLength(0);
    expect(screen.getByText(w.selected(2))).toBeTruthy(); // still ticked
  });

  it("says it is working, and sends one request when pressed twice", async () => {
    const api = await ask("Mira Voss", "Brother Anselm");
    const yes = screen.getByRole("button", { name: w.mergeManyYes });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect(await within(screen.getByRole("dialog")).findByRole("button", { name: w.mergeManyBusy })).toBeTruthy();
    await screen.findByText(w.mergedMany("Brother Anselm"));
    expect(merges(api)).toHaveLength(1);
  });

  it("says a refusal in plain words and keeps the question open to try again", async () => {
    const api = mockApi("table");
    api.changeName = async () => {
      throw new ApiError("network");
    };
    open(DM, api);
    await ready();
    tick("Mira Voss");
    tick("Brother Anselm");
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    fireEvent.click(await screen.findByRole("button", { name: w.mergeManyYes }));
    await waitFor(() =>
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(w.errors.network),
    );
    expect(screen.getByRole("button", { name: w.mergeManyYes })).toBeTruthy();
  });

  it("says nothing was merged when the bot refuses the whole merge, and keeps the question open", async () => {
    const api = mockApi("table");
    api.changeName = async () => {
      throw new ApiError("bad-target");
    };
    open(DM, api);
    await ready();
    tick("Mira Voss");
    tick("Brother Anselm");
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    fireEvent.click(await screen.findByRole("button", { name: w.mergeManyYes }));
    await waitFor(() =>
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(w.mergeManyRefused),
    );
    expect(screen.getByRole("button", { name: w.mergeManyYes })).toBeTruthy(); // try another keeper
  });

  it("when the bot hasn't answered in time, says it may still happen and starts the ticking over", async () => {
    const api = mockApi("table");
    api.changeName = async () => {
      throw new ApiError("still-waiting");
    };
    open(DM, api);
    await ready();
    tick("Mira Voss");
    tick("Brother Anselm");
    fireEvent.click(screen.getByRole("button", { name: w.mergeSelected }));
    fireEvent.click(await screen.findByRole("button", { name: w.mergeManyYes }));
    await waitFor(() =>
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(w.errors["still-waiting"]),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText(w.selected(2))).toBeNull();
  });
});

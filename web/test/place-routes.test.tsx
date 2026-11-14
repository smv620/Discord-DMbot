/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's "Next to…" and "Travel to…" on the Names page (#4341): searched for places only, the card's place first, each choice saves its
// own sentence, Back saves nothing, a player or a non-place gets neither, and the switch off changes nothing.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type NamesPage, type WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { NEXT_CHOICES, placeRoutesWords as w, TRAVEL_CHOICES } from "../src/content/place-routes-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/place-routes-switch", () => ({
  get PLACE_ROUTES_LIVE() {
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

async function openRow(name: string, path = DM) {
  const api = mockApi("table");
  const changes: WebLinkChange[] = [];
  const queries: { q: string; kind: string }[] = [];
  const all = api.allNames!.bind(api);
  api.allNames = async (ref, query) => {
    queries.push({ q: query.q, kind: query.kind });
    return all(ref, query);
  };
  api.changeWebLink = async (_ref, change) => {
    changes.push(change);
    return { a: "x", b: "y", label: "", more: 0, links: [] };
  };
  render(<Names api={api} path={path} />);
  await screen.findByRole("list", { name: namesPage.listLabel });
  fireEvent.change(screen.getByLabelText(namesPage.perPageLabel), { target: { value: "200" } });
  await waitFor(() => expect(rowOf(name)).toBeTruthy());
  const row = rowOf(name);
  fireEvent.click(within(row).getByRole("button", { name }));
  return { api, row: within(row), changes, queries };
}

describe("the two actions", () => {
  it("show for a DM on a place, with Discord's words", async () => {
    const { row } = await openRow("Saltmarsh");
    expect(await row.findByRole("button", { name: w.nextButton })).toBeTruthy();
    expect(row.getByRole("button", { name: w.travelButton })).toBeTruthy();
  });
  it("do not show on a name that is not a place", async () => {
    const { row } = await openRow("Ser Alaric-of-the-Many-Hills-and-Rivers-Beyond-the-Greywatch-Wall");
    expect(row.queryByRole("button", { name: w.nextButton })).toBeNull();
  });
  it("do not show while the switch is off", async () => {
    live.on = false;
    const { row } = await openRow("Saltmarsh");
    expect(row.queryByRole("button", { name: w.nextButton })).toBeNull();
  });
});

describe("saving", () => {
  it("searches places only, asks the card's question, and a choice saves its own sentence with the card's place first", async () => {
    const { row, changes, queries } = await openRow("Saltmarsh");
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    const found = await row.findByRole("button", { name: "Greywatch" });
    await waitFor(() => expect(queries.at(-1)).toEqual({ q: "Greywatch", kind: "place" }));
    const other = found.textContent as string;
    fireEvent.click(found);
    expect(await row.findByText(w.ask("Saltmarsh", other, false))).toBeTruthy();
    fireEvent.click(row.getByLabelText(new RegExp(`^${NEXT_CHOICES[2]!.label.replace(/[()]/g, "\\$&")}`)));
    fireEvent.click(row.getByRole("button", { name: w.save }));
    await waitFor(() => expect(changes).toHaveLength(1));
    expect(changes[0]).toMatchObject({ action: "add", words: "is on the shore of", secret: false });
    expect(await row.findByText(w.saved("Saltmarsh", other, NEXT_CHOICES[2]!))).toBeTruthy();
  });
  it("travel saves its way, 'Not sure' included", async () => {
    const { row, changes } = await openRow("Saltmarsh");
    fireEvent.click(await row.findByRole("button", { name: w.travelButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", true)), { target: { value: "Greywatch" } });
    fireEvent.click(await row.findByRole("button", { name: "Greywatch" }));
    fireEvent.click(row.getByLabelText(new RegExp(`^${TRAVEL_CHOICES.at(-1)!.label}`)));
    fireEvent.click(row.getByRole("button", { name: w.save }));
    await waitFor(() => expect(changes).toHaveLength(1));
    expect(changes[0]).toMatchObject({ words: "is connected to" });
  });
  it("Back saves nothing, and Save waits for a choice", async () => {
    const { row, changes } = await openRow("Saltmarsh");
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    fireEvent.click(await row.findByRole("button", { name: "Greywatch" }));
    expect((row.getByRole("button", { name: w.save }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(row.getByRole("button", { name: w.back }));
    expect(changes).toHaveLength(0);
    expect(await row.findByRole("button", { name: w.nextButton })).toBeTruthy();
  });
  it("a second press of Save while the first is still saving sends one change", async () => {
    const { api, row, changes } = await openRow("Saltmarsh");
    let release!: () => void;
    api.changeWebLink = async (_ref, change) => {
      changes.push(change);
      const wait = new Promise<void>((r) => (release = r));
      await wait;
      return { a: "x", b: "y", label: "", more: 0, links: [] };
    };
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    fireEvent.click(await row.findByRole("button", { name: "Greywatch" }));
    fireEvent.click(row.getByLabelText(/^Next to/));
    // Native clicks: fireEvent flushes the re-render, which would disable Save before the second press.
    const save = row.getByRole("button", { name: w.save }) as HTMLButtonElement;
    save.click();
    save.click();
    release();
    await waitFor(() => expect(changes).toHaveLength(1));
    expect(await row.findByText(w.saved("Saltmarsh", "Greywatch", NEXT_CHOICES[0]!))).toBeTruthy();
    expect(changes).toHaveLength(1);
  });
  it("an old search answer never replaces a newer one", async () => {
    const { api, row } = await openRow("Saltmarsh");
    const all = api.allNames!.bind(api);
    const held: { answer?: (page: NamesPage) => void } = {};
    api.allNames = async (ref, query) => {
      if (query.q === "Grey") return new Promise<NamesPage>((r) => (held.answer = r));
      return all(ref, query);
    };
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Grey" } });
    await waitFor(() => expect(held.answer).toBeDefined(), { timeout: 2000 });
    fireEvent.input(row.getByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    await row.findByRole("button", { name: "Greywatch" }, { timeout: 2000 });
    // The old answer arrives late, with a place of its own; it must not appear.
    const sample = await all(DM.split("/").pop()!, { q: "Greywatch", kind: "place", sort: "name", page: 1 });
    held.answer!({ ...sample, names: sample.names.slice(0, 1).map((n) => ({ ...n, name: "Old Name" })) });
    await new Promise((r) => setTimeout(r, 0));
    expect(row.queryByRole("button", { name: "Old Name" })).toBeNull();
    expect(row.getByRole("button", { name: "Greywatch" })).toBeTruthy();
  });
});

describe("when saving fails", () => {
  it("says so and keeps the choice", async () => {
    const { api, row } = await openRow("Saltmarsh");
    api.changeWebLink = async () => {
      throw new ApiError("unavailable");
    };
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    fireEvent.click(await row.findByRole("button", { name: "Greywatch" }));
    fireEvent.click(row.getByLabelText(/^Next to/));
    fireEvent.click(row.getByRole("button", { name: w.save }));
    expect(await row.findByText(w.failed)).toBeTruthy();
  });
  it("a refused save shows the refusal and keeps the choice", async () => {
    const { api, row } = await openRow("Saltmarsh");
    api.changeWebLink = async () => {
      throw new ApiError("bad-target");
    };
    fireEvent.click(await row.findByRole("button", { name: w.nextButton }));
    fireEvent.input(await row.findByLabelText(w.asked("Saltmarsh", false)), { target: { value: "Greywatch" } });
    fireEvent.click(await row.findByRole("button", { name: "Greywatch" }));
    fireEvent.click(row.getByLabelText(/^Next to/));
    fireEvent.click(row.getByRole("button", { name: w.save }));
    expect(await row.findByText(w.refused)).toBeTruthy();
    expect((row.getByLabelText(/^Next to/) as HTMLInputElement).checked).toBe(true);
  });
});

describe("the words", () => {
  it("each hint is the card's own sentence, two-way ones with both places first", () => {
    expect(w.hint("Targos", "Bryn Shander", NEXT_CHOICES[2]!)).toBe("Targos is on the shore of Bryn Shander");
    expect(w.hint("Targos", "Bryn Shander", TRAVEL_CHOICES[0]!)).toBe("Targos and Bryn Shander are connected by road");
    expect(w.ask("Targos", "Bryn Shander", true)).toBe("How do you get from Targos to Bryn Shander? Pick one.");
    expect(w.asked("Targos", false)).toBe("Targos is next to…");
    expect(w.asked("Targos", true)).toBe("Travel from Targos to…");
  });
});

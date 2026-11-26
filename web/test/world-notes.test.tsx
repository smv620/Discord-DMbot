/** @jsxImportSource preact */
// @vitest-environment happy-dom
// World notes on the Names page (#4285): DMbot's guesses for the DM to keep or turn down, in Discord's words, one Undo for each press,
// a line checked meanwhile says so, and nothing at all while the switch is off or for a player.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import { namesPage } from "../src/content/names-words";
import { worldNotesWords as w } from "../src/content/world-notes-words";

const live = vi.hoisted(() => ({ on: true, roads: false, feelings: false }));
// The Feelings group (#4959) is behind its own switch, off unless a test turns it on.
vi.mock("../src/content/world-feelings-switch", () => ({
  get WORLD_FEELINGS_LIVE() {
    return live.feelings;
  },
}));
vi.mock("../src/content/world-notes-switch", () => ({
  get WORLD_NOTES_LIVE() {
    return live.on;
  },
}));
// The Roads group (#4784) is behind its own switch, off unless a test turns it on.
vi.mock("../src/content/roads-switch", () => ({
  get ROADS_LIVE() {
    return live.roads;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
  live.roads = false;
  live.feelings = false;
});

const DM = "/account/names/100.cmp1";
const SALT = "Saltmarsh: Swamp";

async function open(path = DM) {
  const api = mockApi("table");
  render(<Names api={api} path={path} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const calls = (api: ReturnType<typeof mockApi>, what: string) => api.calls.filter((c) => c.startsWith(`changeWorldNotes:`) && c.includes(what));

describe("the list", () => {
  it("shows each kind's lines in Discord's words, with where each came from", async () => {
    await open();
    expect(await screen.findByText(SALT)).toBeTruthy();
    expect(screen.getByText("Saltmarsh: Mild and wet")).toBeTruthy();
    expect(screen.getByText("Greywatch: Ruled by The Greywatch Guard")).toBeTruthy();
    expect(screen.getByText("Brother Venn: Giant")).toBeTruthy();
    expect(screen.getAllByText(/Land · from a session/).length).toBeGreaterThan(0);
    expect(screen.getByText("Language · from your lore")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepAllPlaces(3) })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepAllLanguages(1) })).toBeTruthy();
  });
  it("says partly, and the table's own words for an \"other\" value, as Discord's card does", () => {
    const base = { name: "Saltmarsh", words: "", how: null, ruler: null };
    expect(w.place({ ...base, layer: "terrain", value: "swamp", how: "partly" })).toBe("Saltmarsh: Partly swamp");
    expect(w.place({ ...base, layer: "terrain", value: "other", words: "Floating islands" })).toBe("Saltmarsh: Floating islands");
    expect(w.place({ ...base, layer: "climate", value: "other", words: "Always misty", how: "wet" })).toBe("Saltmarsh: Always misty and wet");
    expect(w.place({ ...base, layer: "climate", value: "cold", how: "dry" })).toBe("Saltmarsh: Cold and dry");
  });
  it("says a road's condition by its road, with the table's own words for an \"other\" value (#5003)", () => {
    const base = { name: "the road between Targos and Bryn Shander", words: "", how: null, ruler: null, layer: "condition" };
    expect(w.place({ ...base, value: "closed" })).toBe("the road between Targos and Bryn Shander: Closed");
    expect(w.place({ ...base, value: "hard" })).toBe("the road between Targos and Bryn Shander: Hard going");
    expect(w.place({ ...base, value: "other", words: "Half washed out" })).toBe("the road between Targos and Bryn Shander: Half washed out");
    expect(w.kindHint.condition).toBe("Road condition");
  });

  it("is not there while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<Names api={api} path={DM} />);
    await screen.findByRole("list", { name: namesPage.listLabel });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("worldNotes:"))).toBe(false);
  });
});

describe("the presses", () => {
  it("✅ Keep sends one line's id, says what was kept, and the line leaves the list", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    expect(await screen.findByText(w.kept(SALT))).toBeTruthy();
    expect(calls(api, '"action":"keep"')).toHaveLength(1);
    await waitFor(() => expect(screen.queryByText(SALT)).toBeNull());
  });
  it("Keep all sends the ids the DM was shown, as one press", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepAllPlaces(3) }));
    expect(await screen.findByText(w.keptMany(3))).toBeTruthy();
    const sent = calls(api, '"action":"keep"');
    expect(sent).toHaveLength(1);
    expect((sent[0]!.match(/place:/g) ?? []).length).toBe(3);
  });
  it("🚫 says DMbot won't guess it again, for a place and for a language", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel(SALT) }));
    expect(await screen.findByText(new RegExp(`${w.notAgain}$`))).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel("Brother Venn: Giant") }));
    expect(await screen.findByText(new RegExp(`${w.notAgain}$`))).toBeTruthy();
  });
  it("one line left in a kind says Keep the 1, as Discord does", () => {
    expect(w.keepAllPlaces(1)).toBe("✅ Keep the 1 place");
    expect(w.keepAllLanguages(1)).toBe("✅ Keep the 1 language");
    expect(w.keepAllPlaces(3)).toBe("✅ Keep all 3 places");
  });
  it("↩ Undo puts the line back", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(await screen.findByText(SALT)).toBeTruthy();
    expect(calls(api, '"action":"undo"')).toHaveLength(1);
  });
  it("a line checked meanwhile says so in plain words and the list is read again", async () => {
    const api = await open();
    const press = fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(SALT) }));
    expect(press).toBeDefined();
    await screen.findByText(w.kept(SALT));
    api.changeWorldNotes = async () => {
      throw new ApiError("gone");
    };
    const before = api.calls.filter((c) => c.startsWith("worldNotes:")).length;
    fireEvent.click(await screen.findByRole("button", { name: w.notRightLabel("Greywatch: Ruled by The Greywatch Guard") }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("worldNotes:")).length).toBeGreaterThan(before));
  });
});

// The Roads group (#4784): a road whose length a kept map disagrees with, ✅ Keep only (no 🚫), behind ROADS_LIVE.
describe("the Roads group", () => {
  const ROAD = "Targos to Bryn Shander";
  const R1 = `road:${"c1".repeat(16)}`;
  const R2 = `road:${"c2".repeat(16)}`;
  const NOTE = "The map puts it three hours' ride apart; the lore says two days.";

  it("shows no Roads group while ROADS_LIVE is off", async () => {
    await open();
    await screen.findByText(SALT);
    expect(screen.queryByRole("heading", { name: w.roads })).toBeNull();
    expect(screen.queryByText(ROAD)).toBeNull();
  });

  it("with the switch off, roads are not counted: a page with only roads says nothing to check", async () => {
    const api = mockApi("table");
    api.worldNotes = async (ref) => {
      api.calls.push(`worldNotes:${ref}`);
      return { places: [], languages: [], roads: [{ id: R1, name: ROAD, note: NOTE, fromSession: null }] };
    };
    render(<Names api={api} path={DM} />);
    expect(await screen.findByText(w.none)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.keepAllRoads(1) })).toBeNull();
  });

  it("lists each road with its name and the API's note, and no Not-right button", async () => {
    live.roads = true;
    await open();
    expect(await screen.findByRole("heading", { name: w.roads })).toBeTruthy();
    expect(screen.getByText(ROAD)).toBeTruthy();
    expect(screen.getByText(NOTE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.notRightLabel(ROAD) })).toBeNull();
  });

  it("Keep sends the road's id and the line goes", async () => {
    live.roads = true;
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(ROAD) }));
    await waitFor(() => expect(calls(api, R1)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText(ROAD)).toBeNull());
    expect(await screen.findByText(w.kept(ROAD))).toBeTruthy();
  });

  it("Keep all sends the shown ids, as one press", async () => {
    live.roads = true;
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepAllRoads(2) }));
    await waitFor(() => expect(calls(api, `"ids":["${R1}","${R2}"]`)).toHaveLength(1));
  });

  it("an API with no roads shows no group", async () => {
    live.roads = true;
    const api = mockApi("table");
    api.worldNotes = async (ref) => {
      api.calls.push(`worldNotes:${ref}`);
      return { places: [], languages: [] };
    };
    render(<Names api={api} path={DM} />);
    await screen.findByRole("heading", { name: w.heading });
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("worldNotes:"))).toBe(true));
    expect(screen.queryByRole("heading", { name: w.roads })).toBeNull();
  });

  it("↩ Undo puts a kept road back", async () => {
    live.roads = true;
    await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(ROAD) }));
    await waitFor(() => expect(screen.queryByText(ROAD)).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(ROAD)).toBeTruthy();
  });

  it("a road has no 🚫 Not right, and the door refuses one if it were sent", async () => {
    live.roads = true;
    const api = mockApi("table");
    await expect(api.changeWorldNotes!("100.cmp1", { action: "not", id: R1 })).rejects.toMatchObject({ kind: "bad-action" });
  });
});

// The Feelings group (#4959): DMbot's guesses of how a people feels toward another, kept, changed or turned down, one Undo for each press.
describe("the Feelings group (#4959)", () => {
  const MITHRAL = `bias:${"d1".repeat(16)}`;
  const LINE = "Mithral Keep is unfriendly toward elves";

  it("the Feelings group is drawn only behind the switch", async () => {
    await open();
    expect(screen.queryByRole("heading", { name: w.feelings })).toBeNull();
  });

  it("a feeling reads with its step and target, and its keep id is the bias id", async () => {
    live.feelings = true;
    await open();
    expect(await screen.findByText(LINE)).toBeTruthy();
    expect(screen.getByText(`Unfriendly · ${w.fromLore}`)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepLabel(LINE) })).toBeTruthy();
  });

  it("Keep, Not right and Keep all take the bias ids", async () => {
    live.feelings = true;
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: w.keepLabel(LINE) }));
    await waitFor(() => expect(calls(api, `"ids":["${MITHRAL}"]`)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText(LINE)).toBeNull());
  });

  it("Change shows the five steps, the current one first, and saves the picked one with an Undo", async () => {
    live.feelings = true;
    const api = await open();
    // Two feelings are listed: the first one is Mithral Keep.
    fireEvent.click((await screen.findAllByRole("button", { name: w.changeFeeling }))[0]!);
    const group = screen.getByRole("group", { name: w.feelingQuestion("Mithral Keep", "elves") });
    const labels = within(group).getAllByRole("button").map((b) => b.textContent);
    expect(labels.slice(0, 5)).toEqual(["Unfriendly", "Hostile", "Wary", "Friendly", "Devoted"]);
    fireEvent.click(within(group).getByRole("button", { name: "Friendly" }));
    await waitFor(() => expect(calls(api, `"action":"bias","id":"${MITHRAL}","step":"Friendly"`)).toHaveLength(1));
    expect(await screen.findByText(w.kept(w.feelingLine("Mithral Keep", "Friendly", "elves")))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("Back closes Change and writes nothing", async () => {
    live.feelings = true;
    const api = await open();
    // Two feelings are listed: the first one is Mithral Keep.
    fireEvent.click((await screen.findAllByRole("button", { name: w.changeFeeling }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: w.feelingBack }));
    expect(screen.queryByRole("group", { name: w.feelingQuestion("Mithral Keep", "elves") })).toBeNull();
    expect(calls(api, "bias")).toHaveLength(0);
  });

  it("a gone feeling says so and re-reads the list", async () => {
    live.feelings = true;
    const api = await open();
    api.changeWorldNotes = async () => {
      throw new ApiError("gone");
    };
    // Two feelings are listed: the first one is Mithral Keep.
    fireEvent.click((await screen.findAllByRole("button", { name: w.changeFeeling }))[0]!);
    const group = screen.getByRole("group", { name: w.feelingQuestion("Mithral Keep", "elves") });
    fireEvent.click(within(group).getByRole("button", { name: "Friendly" }));
    expect(await screen.findByText(w.gone)).toBeTruthy();
  });
});

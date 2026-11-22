/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A route place's condition on its place panel (#5010, #4996): shown for a route place, with its line and menu; not for another place; nothing
// with the switch off. The menu sends on "place".
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NameRow, PlaceWorld } from "../src/account/api";
import PlaceWorldPanel from "../src/account/PlaceWorld";
import { mockApi } from "../src/account/mock";
import { roadConditionWords as w } from "../src/content/road-condition-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/road-condition-switch", () => ({
  get ROAD_CONDITION_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
const PLACE = "place1".padEnd(32, "0");
const row = { id: PLACE, name: "Greywatch" } as unknown as NameRow;
const act = async () => null;

function world(over: Partial<PlaceWorld>): PlaceWorld {
  return { level: null, form: null, rows: [], groups: [], ...over };
}

function panel(answer: PlaceWorld) {
  const api = mockApi("table");
  api.placeWorld = async () => {
    api.calls.push(`placeWorld:${REF}:${PLACE}`);
    return answer;
  };
  render(<PlaceWorldPanel api={api} refId={REF} row={row} act={act} />);
  return api;
}

describe("a route place's condition on its panel (#5010)", () => {
  it("a route place with a condition shows its line and the DM's menu", async () => {
    panel(world({ road: true, condition: { value: "hard", words: "", since: 3, before: null } }));
    expect(await screen.findByText(w.line("hard", "", 3, null))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
  });

  it("a route place with no condition shows the menu only", async () => {
    panel(world({ road: true }));
    expect(await screen.findByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.queryByText(/🚧 Condition:/)).toBeNull();
  });

  it("a place with no road shows nothing of it", async () => {
    const api = panel(world({}));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("placeWorld:"))).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("nothing is drawn while ROAD_CONDITION_LIVE is off", async () => {
    live.on = false;
    const api = panel(world({ road: true, condition: { value: "closed", words: "", since: null, before: null } }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("placeWorld:"))).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("the menu sends on: place with the place's id", async () => {
    const api = panel(world({ road: true }));
    fireEvent.click(await screen.findByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: `${w.choices[0]!.label}: ${w.choices[0]!.hint}` }));
    await waitFor(() => expect(api.calls).toContain(`roadCondition:${REF}:place:${PLACE}:open`));
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A road's condition in the connection panel (#4451, page half): the DM's menu, DMbot's read, Keep, Show and Hide, and Undo; the players see
// the line only; nothing while ROAD_CONDITION_LIVE is off.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoadCondition as Condition } from "../src/account/api";
import RoadCondition from "../src/account/RoadCondition";
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
const ID = "link1".padEnd(32, "0");
const CLOSED: Condition = { value: "closed", words: "", since: 5, before: { value: "open", words: "" }, read: true, shown: false };

function panel(over: { condition?: Condition; canEdit?: boolean; road?: boolean; api?: ReturnType<typeof mockApi> } = {}) {
  const api = over.api ?? mockApi("table");
  const props = {
    api,
    refId: REF,
    on: "connection" as const,
    id: ID,
    name: "Icewind Pass",
    condition: over.condition,
    road: over.road ?? true,
    canEdit: over.canEdit ?? true,
    onChanged: () => undefined,
  };
  return { api, ...render(<RoadCondition {...props} />) };
}

describe("a road's condition in the connection panel (#4451)", () => {
  it("shows the line with DMbot's read note, and the DM's menu, Keep, and Show", () => {
    panel({ condition: CLOSED });
    expect(screen.getByText(w.line("closed", "", 5, { value: "open", words: "" }) + w.readNote)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.conditionRight })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.show })).toBeTruthy();
  });

  it("the players see the line only: no read note, no menu, no buttons", () => {
    panel({ condition: { ...CLOSED, shown: true }, canEdit: false });
    expect(screen.getByText(w.line("closed", "", 5, { value: "open", words: "" }))).toBeTruthy();
    expect(screen.queryByText(w.readNote, { exact: false })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("a choice sends the value and says what was noted, with Undo", async () => {
    const { api } = panel({});
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: `${w.choices[2]!.label}: ${w.choices[2]!.hint}` }));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("roadCondition:") && c.endsWith(":closed"))).toHaveLength(1));
    expect(await screen.findByText(w.noted("Icewind Pass", "closed", "", null))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.undoLabel })).toBeTruthy();
  });

  it("Something else sends the DM's own words, at most 60 characters, and needs some", async () => {
    const { api } = panel({});
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    const other = screen.getByRole("button", { name: w.choices[4]!.label }) as HTMLButtonElement;
    expect(other.disabled).toBe(true);
    fireEvent.input(screen.getByPlaceholderText(w.choices[4]!.hint), { target: { value: "half washed out at the ford" } });
    expect(other.disabled).toBe(false);
    fireEvent.click(other);
    await waitFor(() => expect(api.calls.some((c) => c.includes(":other"))).toBe(true));
    expect(await screen.findByText(w.noted("Icewind Pass", "other", "half washed out at the ford", null))).toBeTruthy();
  });

  it("Condition is right keeps it, and Show sends the opposite of what is shown", async () => {
    const api = mockApi("table");
    const calls: string[] = [];
    api.keepRoadCondition = async (_ref, id) => {
      calls.push(`keep:${id}`);
    };
    api.showRoadCondition = async (_ref, id, shown) => {
      calls.push(`show:${id}:${shown}`);
    };
    panel({ condition: CLOSED, api });
    fireEvent.click(screen.getByRole("button", { name: w.conditionRight }));
    await waitFor(() => expect(calls).toEqual([`keep:${ID}`]));
    await waitFor(() => expect((screen.getByRole("button", { name: w.show }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.show }));
    await waitFor(() => expect(calls).toEqual([`keep:${ID}`, `show:${ID}:true`]));
  });

  it("Undo takes the change back by its batch", async () => {
    const { api } = panel({});
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: `${w.choices[0]!.label}: ${w.choices[0]!.hint}` }));
    const undo = await screen.findByRole("button", { name: w.undoLabel });
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    await waitFor(() => expect(api.calls).toContain("undoRouteLength:" + REF + ":90"));
  });

  it("a gone condition says so", async () => {
    const api = mockApi("table");
    const { ApiError } = await import("../src/account/api");
    api.roadCondition = async () => {
      throw new ApiError("gone");
    };
    panel({ api });
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: `${w.choices[0]!.label}: ${w.choices[0]!.hint}` }));
    expect((await screen.findByRole("alert")).textContent?.trim()).toBe(w.gone);
  });

  it("after a change the new line shows at once, and Undo puts the old one back", async () => {
    panel({ condition: CLOSED });
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    fireEvent.click(screen.getByRole("button", { name: `${w.choices[0]!.label}: ${w.choices[0]!.hint}` }));
    expect(await screen.findByText(w.line("open", "", null, { value: "closed", words: "" }) + w.readNote)).toBeTruthy();
    const undo = await screen.findByRole("button", { name: w.undoLabel });
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    expect(await screen.findByText(w.line("closed", "", 5, { value: "open", words: "" }) + w.readNote)).toBeTruthy();
  });

  it("nothing is drawn while ROAD_CONDITION_LIVE is off", () => {
    live.on = false;
    const { container } = panel({ condition: CLOSED });
    expect(container.textContent).toBe("");
  });
});

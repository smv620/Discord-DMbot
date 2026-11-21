/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The lineage and class line on a character in the Story Graph's node panel (#3925): one line in the card's words, DM only, nothing drawn
// for a character with none, for a failed or refused read, or while NODE_TRAITS_LIVE is off.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type NodeTrait } from "../src/account/api";
import NodeTraits, { traitsLine } from "../src/account/NodeTraits";
import { mockApi } from "../src/account/mock";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/node-traits-switch", () => ({
  get NODE_TRAITS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
const CERRIC = "pc-cerric";
const GUARD = "npc-guard-1";

describe("the lineage and class line on a character (#3925)", () => {
  it("a DM's character shows lineage and class in the card's words, species first", async () => {
    const api = mockApi("table");
    const { container } = render(<NodeTraits api={api} refId={REF} id={CERRIC} />);
    expect(await screen.findByText("🧬 Lineage: Dwarf · Class: Cleric [Legacy 2014], Skald (not in DMbot's rules)")).toBeTruthy();
    expect(container.textContent).toContain("Lineage: Dwarf");
  });

  it("a 2014 entry is tagged, and a word the rules do not have says so", () => {
    const traits: NodeTrait[] = [
      { kind: "class", name: "Cleric", known: true, edition: "2014" },
      { kind: "class", name: "Skald", known: false, edition: "" },
    ];
    expect(traitsLine(traits)).toBe("Class: Cleric [Legacy 2014], Skald (not in DMbot's rules)");
    expect(traitsLine([{ kind: "species", name: "Dwarf", known: true, edition: "2024" }])).toBe("🧬 Lineage: Dwarf");
  });

  it("a character with none draws nothing", async () => {
    const api = mockApi("table");
    const { container } = render(<NodeTraits api={api} refId={REF} id={GUARD} />);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("nodeTraits:"))).toBe(true));
    expect(container.textContent).toBe("");
  });

  it("a failed or refused read draws nothing", async () => {
    const refused = mockApi("table");
    refused.nodeTraits = async () => {
      throw new ApiError("not-allowed");
    };
    const { container, unmount } = render(<NodeTraits api={refused} refId={REF} id={CERRIC} />);
    await waitFor(() => expect(container.textContent).toBe(""));
    unmount();

    const failed = mockApi("table");
    failed.nodeTraits = async () => {
      throw new ApiError("network");
    };
    const second = render(<NodeTraits api={failed} refId={REF} id={CERRIC} />);
    await waitFor(() => expect(second.container.textContent).toBe(""));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("the door refuses a player's view, so the player never sees it", async () => {
    const api = mockApi("table");
    await expect(api.nodeTraits!("100.cmp1.web-player", CERRIC)).rejects.toMatchObject({ kind: "not-allowed" });
  });

  it("nothing is drawn or read with the switch off", async () => {
    live.on = false;
    const api = mockApi("table");
    const { container } = render(<NodeTraits api={api} refId={REF} id={CERRIC} />);
    expect(container.textContent).toBe("");
    expect(api.calls.some((c) => c.startsWith("nodeTraits:"))).toBe(false);
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The card summary (#5051, page half of #5052): its three lines from the API, the words for a sentence still being written,
// nothing for an absent value or a player's card, and nothing at all with CARD_SUMMARY_LIVE off. The mock's three characters
// are read through the Names list (the DM's view only).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CardSummaryFields } from "../src/account/api";
import CardSummary from "../src/account/CardSummary";
import { mockApi } from "../src/account/mock";
import { cardSummaryWords as w } from "../src/content/card-summary-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/card-summary-switch", () => ({
  get CARD_SUMMARY_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "200000000000000001.cmp1";

describe("the card summary", () => {
  it("shows each line the API sends, in order", () => {
    const { container } = render(<CardSummary card={{ who: "A ferrywoman.", firstMet: "First met: the ford", lastMentioned: "Last mentioned: last night" }} />);
    const lines = Array.from(container.querySelectorAll("p")).map((p) => p.textContent);
    expect(lines).toEqual(["A ferrywoman.", "First met: the ford", "Last mentioned: last night"]);
  });

  it("says DMbot is still writing the sentence while it is pending", () => {
    render(<CardSummary card={{ who: null, whoPending: true }} />);
    expect(screen.getByText(w.pending)).toBeTruthy();
  });

  it("draws nothing for an absent or empty value, and nothing for a player's card (no fields)", () => {
    const { container } = render(<CardSummary card={{ who: "", firstMet: null }} />);
    expect(container.textContent).toBe("");
    cleanup();
    const player: CardSummaryFields = {};
    const again = render(<CardSummary card={player} />);
    expect(again.container.textContent).toBe("");
  });

  it("the mock's lore-only character shows Last mentioned and no first-met line", async () => {
    const api = mockApi("table");
    const rows = await api.allNames!(REF, { q: "Aldric", kind: "", sort: "name" as const, page: 1 });
    const row = rows.names.find((n) => n.name === "Aldric Thorn")!;
    expect(row.who).toBe("In your lore: a ferryman on the Brynwater road.");
    expect(row.firstMet).toBeUndefined();
    expect(row.lastMentioned).toBe("Not mentioned at the table yet.");
    const { container } = render(<CardSummary card={row} />);
    const lines = Array.from(container.querySelectorAll("p")).map((p) => p.textContent);
    // The lore line first, then "Not mentioned at the table yet." exactly as sent, with no prefix of the page's own.
    expect(lines).toEqual(["In your lore: a ferryman on the Brynwater road.", "Not mentioned at the table yet."]);
    expect(container.textContent).not.toContain("First met");
  });

  it("the mock's pending character shows the writing line", async () => {
    const api = mockApi("table");
    const rows = await api.allNames!(REF, { q: "Pell", kind: "", sort: "name" as const, page: 1 });
    const row = rows.names.find((n) => n.name === "Old Pell")!;
    expect(row.whoPending).toBe(true);
    render(<CardSummary card={row} />);
    expect(screen.getByText(w.pending)).toBeTruthy();
  });

  it("with the switch off, draws nothing", () => {
    live.on = false;
    const { container } = render(<CardSummary card={{ who: "A ferrywoman.", firstMet: "the ford" }} />);
    expect(container.textContent).toBe("");
  });
});

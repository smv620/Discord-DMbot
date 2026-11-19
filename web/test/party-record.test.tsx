/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The party record on a player character's row (#4778 a2, #4798): the stretches in the party in core's words, "Has left the party"
// with why and Undo, "Back in the party" with what DMbot asks, and the errors in words. DM only; nothing is drawn while
// PARTY_RECORD_LIVE is off.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import PartyRecord from "../src/account/PartyRecord";
import { dayOf, partyRecordWords as w } from "../src/content/party-record-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/party-record-switch", () => ({
  get PARTY_RECORD_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
});

const REF = "100.cmp1";
const WREN = { id: "p1".repeat(16), name: "Wren" };
const ASH = { id: "p2".repeat(16), name: "Ash" };

async function openCard(row = WREN, api = mockApi("table")) {
  render(<PartyRecord api={api} refId={REF} row={row} />);
  fireEvent.click(screen.getByRole("button", { name: w.open }));
  return api;
}

describe("the party record on a player character", () => {
  it("shows when the character was in the party, in core's words with the page's own date", async () => {
    await openCard();
    expect(await screen.findByText(w.since(dayOf(1700000000), 2))).toBeTruthy();
    expect(screen.queryByText(/Left the party on/)).toBeNull();
  });

  it("shows a stretch that ended, with its why", async () => {
    await openCard(ASH);
    expect(await screen.findByText(w.leftOn(dayOf(1705000000), "left"))).toBeTruthy();
    expect(screen.getByText(w.since(dayOf(1690000000), 1))).toBeTruthy();
  });

  it("Has left the party asks why, then says it and offers Undo; Undo says the character is in the party again", async () => {
    const api = await openCard();
    fireEvent.click(await screen.findByRole("button", { name: w.leaveButton }));
    expect(screen.getByText(w.why("Wren"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.reasons.left }));
    expect(await screen.findByText(w.leftDone("Wren", "left"))).toBeTruthy();
    expect(api.calls).toContain(`partyLeave:${REF}:${WREN.id}:left`);
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undoneLeave("Wren"))).toBeTruthy();
  });

  it("a second press while a leave waits sends nothing", async () => {
    const api = await openCard();
    fireEvent.click(await screen.findByRole("button", { name: w.leaveButton }));
    const moved = screen.getByRole("button", { name: w.reasons.left });
    fireEvent.click(moved);
    fireEvent.click(moved);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("partyLeave:"))).toHaveLength(1));
  });

  it("Back in the party for someone who was asked what they know offers Undo, and Undo says they are out again", async () => {
    await openCard(ASH);
    fireEvent.click(await screen.findByRole("button", { name: w.backButton }));
    expect(await screen.findByText(w.backDone("Ash", "join"))).toBeTruthy();
    // The card re-reads after each change and its buttons wait for it: press Undo once it is enabled.
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undoneBack("Ash"))).toBeTruthy();
  });

  it("Back in the party with nothing asked says it and offers no Undo", async () => {
    const api = mockApi("table");
    api.partyBack = async () => ({ batch: null, asked: "none" });
    await openCard(ASH, api);
    fireEvent.click(await screen.findByRole("button", { name: w.backButton }));
    expect(await screen.findByText(w.backDone("Ash", "none"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("Back in the party with the bot asking what they remember says the 'back' line", async () => {
    const api = mockApi("table");
    api.partyBack = async () => ({ batch: null, asked: "back" });
    await openCard(ASH, api);
    fireEvent.click(await screen.findByRole("button", { name: w.backButton }));
    expect(await screen.findByText(w.backDone("Ash", "back"))).toBeTruthy();
  });

  it("an undo too late for a return says to use Has left the party on the card", async () => {
    const api = mockApi("table");
    await openCard(ASH, api);
    fireEvent.click(await screen.findByRole("button", { name: w.backButton }));
    await screen.findByText(w.backDone("Ash", "join"));
    api.partyUndo = async () => {
      throw new ApiError("undo-late");
    };
    await waitFor(() => expect((screen.getByRole("button", { name: w.undo }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.tooLateBack("Ash"))).toBeTruthy();
  });

  it("the errors are said in words, and nothing else changes", async () => {
    const api = mockApi("table");
    api.partyLeave = async () => {
      throw new ApiError("not-in-party");
    };
    await openCard(WREN, api);
    fireEvent.click(await screen.findByRole("button", { name: w.leaveButton }));
    fireEvent.click(screen.getByRole("button", { name: w.reasons.died }));
    expect(await screen.findByText(w.notInParty("Wren"))).toBeTruthy();
    expect(await screen.findByText(w.since(dayOf(1700000000), 2))).toBeTruthy();
  });

  it("a player's page is refused with a plain line and no buttons", async () => {
    const api = mockApi("table");
    render(<PartyRecord api={api} refId="100.cmp1.web-player" row={WREN} />);
    fireEvent.click(screen.getByRole("button", { name: w.open }));
    expect(await screen.findByText(w.failed)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.leaveButton })).toBeNull();
  });

  it("with the switch off, nothing is drawn on the Names row and no call is made", async () => {
    const api = mockApi("table");
    render(<Names api={api} path={`/account/names/${REF}`} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: w.open })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("partyRecord:"))).toBe(false);
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The line the players are told, on a name a recap asks to check (#3833, behind PLAYERS_LINE_LIVE): shown whole under its
// label with a way to change it; nothing at all when there is none or the switch is off; a name with no line shows nothing.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import PlayersKnow from "../src/account/PlayersKnow";
import { playersKnowWords as w } from "../src/content/players-line-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/players-line-switch", () => ({
  get PLAYERS_LINE_LIVE() {
    return live.on;
  },
}));
afterEach(() => {
  cleanup();
  live.on = true;
});

const LONG = "The one-eyed smith of Eastmarch, who forged the king's sword and has never once told anyone where the ore came from.";

describe("Players know", () => {
  it("shows the whole line as plain text, with a link that opens the name on the Names page", () => {
    render(<PlayersKnow line={LONG} campaignRef="100.cmp1" name="Brannoch" />);
    expect(screen.getByText(w.label)).toBeTruthy();
    expect(document.querySelector(".players-know")?.textContent).toContain(LONG); // (never cut)
    const link = screen.getByRole("link", { name: w.changeFor("Brannoch") }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/account/names/100.cmp1#said=Brannoch");
  });
  it("is read as text, never as HTML", () => {
    render(<PlayersKnow line="A <b>bold</b> smith." campaignRef="100.cmp1" name="Brannoch" />);
    expect(document.querySelector(".players-know b")).toBeNull();
  });
  it("draws nothing with no line, a blank one, or the switch off", () => {
    const { container, rerender } = render(<PlayersKnow line={null} campaignRef="100.cmp1" name="Brannoch" />);
    expect(container.innerHTML).toBe("");
    rerender(<PlayersKnow line="   " campaignRef="100.cmp1" name="Brannoch" />);
    expect(container.innerHTML).toBe("");
    live.on = false;
    rerender(<PlayersKnow line="A smith." campaignRef="100.cmp1" name="Brannoch" />);
    expect(container.innerHTML).toBe("");
  });
});

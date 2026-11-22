/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The first meeting's reminder on a waiting claim's card (#5000, page half of #4942): with FIRST_FEELING_LIVE on, the line
// shows under the claim's question with its names in bold and an escaped mark plain; with it off, nothing shows.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WaitingClaim } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

const live = vi.hoisted(() => ({ feeling: true }));
vi.mock("../src/content/web-waiting", async (original) => {
  const real = await original<typeof import("../src/content/web-waiting")>();
  return { ...real, WEB_WAITING_LIVE: true };
});
vi.mock("../src/content/first-feeling-switch", () => ({
  get FIRST_FEELING_LIVE() {
    return live.feeling;
  },
}));

afterEach(() => {
  cleanup();
  live.feeling = true;
});

const REF = "100.cmp1";
const claim: WaitingClaim = {
  id: "c1",
  statement: "Mira Voss knows Far Hall",
  canSecret: true,
  startingFeeling: "🤔 **Mithral Keep** (Tala's home) is unfriendly toward dwarves, and \\*plain\\* is an escape.",
};

async function open() {
  const api = mockApi("table");
  const real = api.webWaiting!.bind(api);
  api.webWaiting = async (ref) => ({ ...(await real(ref)), claims: [claim] });
  render(<WebWaiting api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: w.heading });
  return (await screen.findByText("Mira Voss knows Far Hall")).closest(".panel") as HTMLElement;
}

describe("the first-feeling line on a waiting claim", () => {
  it("with the switch on, shows the line with its names in bold and an escaped mark plain", async () => {
    const card = await open();
    const line = card.querySelector("p.small")!;
    expect(line.textContent).toBe("🤔 Mithral Keep (Tala's home) is unfriendly toward dwarves, and *plain* is an escape.");
    const bold = Array.from(line.querySelectorAll("strong")).map((s) => s.textContent);
    expect(bold).toEqual(["Mithral Keep"]);
    // No HTML from the words: nothing is an element but the bold name.
    expect(line.querySelectorAll("*").length).toBe(1);
  });

  it("with the switch off, shows nothing of it", async () => {
    live.feeling = false;
    const card = await open();
    expect(card.textContent).not.toContain("Mithral Keep");
    expect(card.querySelectorAll("strong").length).toBe(0);
  });
});

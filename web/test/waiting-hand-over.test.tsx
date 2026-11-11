/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A waiting connection that means an item changed hands (#4122): the bracket after it ("Claude sold it", the words of Discord's
// card), and no Keep here until the page can show what keeping it ends: it says to keep it in Discord. Off with HAND_OVER_LIVE.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { handBracket, handKeepInDiscord } from "../src/content/hand-over-words";
import { waitingWords as w } from "../src/content/web-waiting";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/hand-over-switch", () => ({
  get HAND_OVER_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const row = (over: object = {}) => ({
  id: "b1",
  subject: "Belleros",
  object: "the Frost Brand",
  how: "owns",
  secret: false,
  upload: "Lore, 24 Oct",
  ready: true,
  waitingOn: [],
  ...over,
});

async function show(over: object) {
  const api = mockApi("table");
  const real = api.webWaiting!.bind(api);
  api.webWaiting = async (ref) => {
    const got = await real(ref);
    return { ...got, backstory: [row(over)], counts: { ...got.counts, backstory: 1 } };
  };
  render(<WebWaiting api={api} campaignRef="100.cmp1" />);
  await screen.findByRole("heading", { name: w.heading });
}

describe("handBracket", () => {
  it("uses Discord's words", () => {
    expect(handBracket("sold", "Claude")).toBe("Claude sold it");
    expect(handBracket("gave", "Claude")).toBe("Claude gave it");
    expect(handBracket("traded", "Claude")).toBe("Claude traded it");
    expect(handBracket("stolen", "Claude")).toBe("taken from Claude");
    expect(handBracket("lost", "Claude")).toBe("Claude lost it");
  });
});

describe("a waiting hand-over", () => {
  it("shows the bracket and sends the DM to Discord to keep it", async () => {
    await show({ handOver: "sold", handOverFrom: "a".repeat(32), handOverName: "Claude" });
    expect(await screen.findByText("Belleros owns the Frost Brand (Claude sold it)")).toBeTruthy();
    expect(screen.getByText(handKeepInDiscord)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.keep })).toBeNull();
    expect(screen.getByRole("button", { name: w.skip })).toBeTruthy();
  });

  it("a plain connection keeps its Keep and has no bracket", async () => {
    await show({});
    expect(await screen.findByText("Belleros owns the Frost Brand")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
    expect(screen.queryByText(handKeepInDiscord)).toBeNull();
  });

  it("one whose previous holder has no name shows as a plain connection", async () => {
    await show({ handOver: "sold", handOverFrom: "a".repeat(32), handOverName: null });
    expect(await screen.findByText("Belleros owns the Frost Brand")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
  });

  it("changes nothing while the switch is off", async () => {
    live.on = false;
    await show({ handOver: "sold", handOverFrom: "a".repeat(32), handOverName: "Claude" });
    expect(await screen.findByText("Belleros owns the Frost Brand")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
  });
});

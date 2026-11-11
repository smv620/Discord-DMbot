/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A waiting connection that means an item changed hands (#4122): the bracket after it ("Claude sold it", the words of Discord's
// card), and no Keep here until the page can show what keeping it ends: it says to keep it in Discord. Shown whenever the door sends the fields (no switch).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { handBracket, handKeepInDiscord } from "../src/content/hand-over-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(() => {
  cleanup();
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

async function show(over: object, more: object[] = []) {
  const api = mockApi("table");
  const real = api.webWaiting!.bind(api);
  api.webWaiting = async (ref) => {
    const got = await real(ref);
    return {
      ...got,
      backstory: [row(over), ...more],
      counts: { ...got.counts, backstory: 1 + more.length },
    };
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
    await show({
      handOver: "sold",
      handOverFrom: "a".repeat(32),
      handOverName: "Claude",
    });
    expect(
      await screen.findByText("Belleros owns the Frost Brand (Claude sold it)"),
    ).toBeTruthy();
    expect(screen.getByText(handKeepInDiscord)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.keep })).toBeNull();
    expect(screen.getByRole("button", { name: w.skip })).toBeTruthy();
  });

  it("a plain connection keeps its Keep and has no bracket", async () => {
    await show({});
    expect(
      await screen.findByText("Belleros owns the Frost Brand"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
    expect(screen.queryByText(handKeepInDiscord)).toBeNull();
  });

  it("one whose previous holder has no name shows as a plain connection", async () => {
    await show({
      handOver: "sold",
      handOverFrom: "a".repeat(32),
      handOverName: null,
    });
    expect(
      await screen.findByText("Belleros owns the Frost Brand"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
  });

  it("Keep all leaves a hand-over out", async () => {
    await show({ id: "b1" }, [
      row({
        id: "b2",
        object: "the Shield",
        handOver: "gave",
        handOverFrom: "a".repeat(32),
        handOverName: "Claude",
      }),
      row({ id: "b3", object: "the Lantern" }),
    ]);
    expect(
      await screen.findByRole("button", { name: w.keepAll(2) }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.keepAll(3) })).toBeNull();
  });

  it("a secret hand-over shows neither Keep button, and still Skip", async () => {
    await show({
      secret: true,
      handOver: "stolen",
      handOverFrom: "a".repeat(32),
      handOverName: "Claude",
    });
    expect(
      await screen.findByText(
        "Belleros owns the Frost Brand (taken from Claude)",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.keepIsSecret })).toBeNull();
    expect(screen.queryByRole("button", { name: w.keep })).toBeNull();
    expect(screen.getByRole("button", { name: w.skip })).toBeTruthy();
  });
});

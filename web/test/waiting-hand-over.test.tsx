/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A waiting connection that means an item changed hands (#4122): the bracket after it ("Claude sold it", the words of Discord's
// card), and no Keep here until the page can show what keeping it ends: it says to keep it in Discord. Shown whenever the door sends the fields (no switch).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { WaitingBackstory } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { handBracket, handKeeps } from "../src/content/hand-over-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(() => {
  cleanup();
});

const row = (over: Partial<WaitingBackstory> = {}): WaitingBackstory => ({
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

async function show(over: Partial<WaitingBackstory>, more: WaitingBackstory[] = []) {
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
  it("shows the bracket, what Keep does to the one before, and a Keep button", async () => {
    await show({
      handOver: "sold",
      handOverFrom: "a".repeat(32),
      handOverName: "Claude",
    });
    expect(
      await screen.findByText("Belleros owns the Frost Brand (Claude sold it)"),
    ).toBeTruthy();
    expect(screen.getByText(handKeeps("sold", "Claude", false))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.skip })).toBeTruthy();
  });

  it("a plain connection keeps its Keep and has no bracket", async () => {
    await show({});
    expect(
      await screen.findByText("Belleros owns the Frost Brand"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keep })).toBeTruthy();
    expect(screen.queryByText(/no longer has it/)).toBeNull();
  });

  it("a theft says only that they no longer have it with them", async () => {
    await show({ handOver: "stolen", handOverFrom: "a".repeat(32), handOverName: "Claude" });
    expect(await screen.findByText(handKeeps("stolen", "Claude", false))).toBeTruthy();
    expect(screen.getByText(/Who owns it doesn't change/)).toBeTruthy();
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

  it("a secret hand-over can be kept (as secret), and says the players still see the one before", async () => {
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
    expect(screen.getByText(handKeeps("stolen", "Claude", true))).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepIsSecret })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.skip })).toBeTruthy();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A connection DMbot inferred from the lore (#3928, page half of #4967): its "Why?" says it is DMbot's guess with the
// reason and never shows the lore's words beside it; the waiting card's guess line is in Discord's words; and a Keep all
// that leaves guesses says how many still need a yes.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { WaitingBackstory, WebLink } from "../src/account/api";
import LinkFeeling from "../src/account/LinkFeeling";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { feelingWords as fw } from "../src/content/knows-feeling-text";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(cleanup);

const REF = "100.cmp1";
const link = (over: Partial<WebLink> = {}): WebLink => ({
  id: "l1",
  words: "knows (wary)",
  secret: false,
  direction: "a-b",
  source: { type: "upload", label: "Frostmaiden notes" },
  ...over,
});
const noAct = async () => null;

describe("a kept inference", () => {
  it("shows Why: DMbot's guess from your lore with the label and the reason, and never a quote", () => {
    const { container } = render(
      <LinkFeeling n={1} link={link({ inferred: "Mira is called Tavin's sister", quote: "Mira and Tavin share a cart" })} feeling="wary" act={noAct} />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain(`Why: DMbot's guess from your lore "Frostmaiden notes": Mira is called Tavin's sister`);
    expect(text).not.toContain("Mira and Tavin share a cart");
    expect(screen.queryByText(fw.quoteCaption)).toBeNull();
  });

  it("with no label it reads Why: DMbot's guess from your lore: <reason>", () => {
    render(<LinkFeeling n={1} link={link({ source: { type: "dm" }, inferred: "Mira is called Tavin's sister" })} feeling="wary" act={noAct} />);
    expect(screen.getByText("Why: DMbot's guess from your lore: Mira is called Tavin's sister")).toBeTruthy();
  });

  it("a connection with no reason shows the quote line as before", () => {
    render(<LinkFeeling n={1} link={link({ quote: "They argued over the toll." })} feeling="wary" act={noAct} />);
    expect(screen.getByText(fw.quoteCaption)).toBeTruthy();
    expect(screen.queryByText(/DMbot's guess from your lore/)).toBeNull();
  });
});

describe("the waiting card and Keep all", () => {
  const base: WaitingBackstory = { id: "c1", subject: "Belleros", object: "Cerric", how: "knows (friendly)", secret: false, upload: "Lore", ready: true, waitingOn: [] };

  async function open(backstory: WaitingBackstory[]) {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    api.webWaiting = async (ref) => ({ ...(await real(ref)), backstory });
    render(<WebWaiting api={api} campaignRef={REF} />);
    await screen.findByRole("heading", { name: w.heading });
    return api;
  }

  it("the guess line is in Discord's words, with no full stop after the reason", async () => {
    await open([{ ...base, guess: "offered him food" }]);
    expect(await screen.findByText("🤔 DMbot's guess: offered him food (your lore only hints at it)")).toBeTruthy();
  });

  it("Keep all that leaves a guess says how many still need a yes", async () => {
    await open([
      { ...base, id: "c1", guess: "offered him food" },
      { ...base, id: "c2", object: "Mara" },
      { ...base, id: "c3", object: "Venn" },
      { ...base, id: "c4", guess: "a second guess", object: "Dorn" },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: w.keepAll(2) }));
    expect(await screen.findByText(w.guessesLeft(2))).toBeTruthy();
    expect(w.guessesLeft(1)).toBe("1 of DMbot's guesses still needs your yes.");
  });

  it("a Keep all with no guess left says nothing about guesses", async () => {
    await open([
      { ...base, id: "c2", object: "Mara" },
      { ...base, id: "c3", object: "Venn" },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: w.keepAll(2) }));
    await screen.findByText(w.kept(2));
    expect(screen.queryByText(/still need/)).toBeNull();
    expect(screen.queryByText(/still needs/)).toBeNull();
  });

  it("the section holds no guess line before any Keep all is pressed", async () => {
    await open([{ ...base, id: "c1", guess: "offered him food" }, { ...base, id: "c2", object: "Mara" }]);
    expect(await screen.findByText(w.guessLine("offered him food"))).toBeTruthy();
    expect(screen.queryByText(/still need/)).toBeNull();
  });
});

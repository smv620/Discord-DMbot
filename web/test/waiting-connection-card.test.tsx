/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The connection card on Waiting for you → From your backstory (#4430, words from #4429): the lines in order, "Update it" or "Add it" kept apart
// from "can players see it?", and the one toggle that changes only the Players line.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WaitingBackstory } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { differs, waitingWords as w } from "../src/content/web-waiting";

vi.mock("../src/content/web-waiting", async (original) => {
  const real = await original<typeof import("../src/content/web-waiting")>();
  return { ...real, WEB_WAITING_LIVE: true };
});

afterEach(cleanup);

const REF = "100.cmp1";
const base: WaitingBackstory = { id: "c1", subject: "Belleros", object: "Cerric", how: "knows (friendly)", secret: false, upload: "Lore", ready: true, waitingOn: [] };

async function open(item: Partial<WaitingBackstory>) {
  const api = mockApi("table");
  const real = api.webWaiting!.bind(api);
  api.webWaiting = async (ref) => ({ ...(await real(ref)), backstory: [{ ...base, ...item }] });
  render(<WebWaiting api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: w.heading });
  const card = (await screen.findByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").startsWith("Proposed: Belleros"))).closest(".panel") as HTMLElement;
  return { api, card };
}
const lines = (card: HTMLElement): string[] => Array.from(card.querySelectorAll("p")).map((p) => p.textContent ?? "");

describe("the connection card", () => {
  it("an update reads Now, Proposed, the guess, the quote, then the Players line", async () => {
    const { card } = await open({ now: { how: "knows", hidden: false }, guess: "offered him food to ease his hunger", quote: "Belleros offered Cerric a goodberry" });
    expect(lines(card).slice(0, 5)).toEqual([
      "Now: Belleros knows Cerric",
      "Proposed: Belleros knows (friendly) Cerric",
      w.guessLine("offered him food to ease his hunger"),
      "From your text: “Belleros offered Cerric a goodberry”",
      w.playersUpdate(false, false),
    ]);
    expect(within(card).getByRole("button", { name: w.updateIt })).toBeTruthy();
    expect(within(card).getByRole("button", { name: w.leaveAsIs })).toBeTruthy();
  });

  it("without a guess the guess line is left out, and the quote follows Proposed", async () => {
    const { card } = await open({ now: { how: "knows", hidden: false }, quote: "q" });
    expect(lines(card).slice(0, 3)).toEqual(["Now: Belleros knows Cerric", "Proposed: Belleros knows (friendly) Cerric", "From your text: “q”"]);
  });

  it("a new connection has no Now line, and says Add it and Leave it out", async () => {
    const { card } = await open({});
    expect(lines(card)[0]).toBe("Proposed: Belleros knows (friendly) Cerric");
    expect(lines(card)).toContain(w.playersNew(false));
    expect(within(card).getByRole("button", { name: w.addIt })).toBeTruthy();
    expect(within(card).getByRole("button", { name: w.leaveOut })).toBeTruthy();
  });

  it("the four Players lines for an update", () => {
    expect(w.playersUpdate(false, false)).toBe("👁️ Players can see it now, and still will after the update.");
    expect(w.playersUpdate(true, true)).toBe("🤫 Hidden from players now, and still will be after the update.");
    expect(w.playersUpdate(false, true)).toBe("🤫 Players can see it now. After the update it will be hidden.");
    expect(w.playersUpdate(true, false)).toBe("👁️ Hidden from players now. After the update they will see it.");
  });

  it("the toggle changes only the Players line and sends nothing until Update it", async () => {
    const { api, card } = await open({ now: { how: "knows", hidden: false } });
    fireEvent.click(within(card).getByRole("button", { name: w.hideFromPlayers }));
    expect(within(card).getByText(w.playersUpdate(false, true))).toBeTruthy();
    expect(within(card).getByRole("button", { name: w.letPlayersSee })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("answerWaiting:"))).toBe(false);
    fireEvent.click(within(card).getByRole("button", { name: w.updateIt }));
    await waitFor(() => expect(api.calls).toContain(`answerWaiting:${REF}:backstory:keep:c1:secret`));
  });

  it("an update of a hidden connection stays hidden by default", async () => {
    const { api, card } = await open({ now: { how: "knows", hidden: true } });
    expect(within(card).getByText(w.playersUpdate(true, true))).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: w.updateIt }));
    await waitFor(() => expect(api.calls).toContain(`answerWaiting:${REF}:backstory:keep:c1:secret`));
  });

  it("a secret name has no toggle and says why", async () => {
    const { card } = await open({ secret: true });
    expect(within(card).getByText(w.playersSecretName)).toBeTruthy();
    expect(within(card).queryByRole("button", { name: w.hideFromPlayers })).toBeNull();
    expect(within(card).queryByRole("button", { name: w.letPlayersSee })).toBeNull();
  });

  it("Keep all says it uses what each card says about players", async () => {
    const api = mockApi("table");
    render(<WebWaiting api={api} campaignRef={REF} />);
    await screen.findByRole("heading", { name: w.heading });
    expect(screen.getAllByText(w.keepAllPlayers).length).toBeGreaterThan(0);
  });
});

describe("Keep all and DMbot's guesses", () => {
  it("a guess is never in Keep all", async () => {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    api.webWaiting = async (ref) => ({
      ...(await real(ref)),
      backstory: [
        { ...base, id: "c1", guess: "offered him food" },
        { ...base, id: "c2", object: "Mara" },
        { ...base, id: "c3", object: "Venn" },
      ],
    });
    render(<WebWaiting api={api} campaignRef={REF} />);
    await screen.findByRole("heading", { name: w.heading });
    expect((await screen.findAllByRole("button", { name: w.keepAll(2) }))[0] as HTMLElement).toBeTruthy();
  });
});

describe("leaving a card", () => {
  it("says Left out. for a new connection and Left as it is. for an update", async () => {
    const first = await open({});
    fireEvent.click(within(first.card).getByRole("button", { name: w.leaveOut }));
    expect(await screen.findByText(w.done.leftOut)).toBeTruthy();
    expect(w.done.leftOut).toBe("Left out.");
    cleanup();
    const second = await open({ now: { how: "knows", hidden: false } });
    fireEvent.click(within(second.card).getByRole("button", { name: w.leaveAsIs }));
    expect(await screen.findByText(w.done.leftAsIs)).toBeTruthy();
    expect(w.done.leftAsIs).toBe("Left as it is.");
  });
});

describe("bolding only what differs (#4429, the owner)", () => {
  const bolds = (card: HTMLElement): string[] => Array.from(card.querySelector("p#wb-c1")!.querySelectorAll("strong")).map((e) => e.textContent ?? "");

  it("knows to knows (friendly) bolds only (friendly), and the names are plain", async () => {
    const { card } = await open({ now: { how: "knows", hidden: false }, how: "knows (friendly)" });
    expect(bolds(card)).toEqual(["(friendly)"]);
  });

  it("knows to ally of bolds ally of, whole", async () => {
    const { card } = await open({ now: { how: "knows", hidden: false }, how: "ally of" });
    expect(bolds(card)).toEqual(["ally of"]);
    expect(card.querySelector("p#wb-c1")?.textContent).toBe("Proposed: Belleros ally of Cerric");
  });

  it("a new card bolds the names and no connection words", async () => {
    const { card } = await open({ how: "knows (friendly)" });
    expect(bolds(card)).toEqual(["Belleros", "Cerric"]);
  });

  it("differs compares word by word", () => {
    expect(differs("knows", "knows (friendly)")).toEqual({ before: "knows", bold: "(friendly)", after: "" });
    expect(differs("ally of", "enemy of")).toEqual({ before: "", bold: "enemy", after: "of" });
    expect(differs("knows", "ally of")).toEqual({ before: "", bold: "ally of", after: "" });
  });
});

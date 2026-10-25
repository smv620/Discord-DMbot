/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "What people believe" on the Story graph (#2412 slice 3b), against the shapes of core PR 2693: the list in
// DMbot's own words, Keep as a belief / Not right / Undo through the bot, the wait shown with its stage, the
// refusals in plain words, and nothing at all for a player or a peek viewer.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebReadings from "../src/account/WebReadings";
import { readingWords as w } from "../src/content/web-readings";

afterEach(cleanup);

const REF = "100.cmp1";
async function open(api: AccountApi & { calls: string[] } = mockApi("table"), ref = REF) {
  render(<WebReadings api={api} campaignRef={ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const card = (words: string): HTMLElement => screen.getByText(words).closest(".panel") as HTMLElement;
const press = (inCard: HTMLElement, name: string | RegExp) => fireEvent.click(within(inCard).getByRole("button", { name }));
const alertText = () => screen.getByRole("alert").textContent;

describe("What people believe", () => {
  it("lists each belief in DMbot's words: what kind, whose, how sure, from which session", async () => {
    await open();
    expect(screen.getByText(w.count(6))).toBeTruthy();
    const c = card("Mira Voss took the key");
    expect(c.textContent).toContain(w.rules["theory"]);
    expect(c.textContent).toContain(w.believedBy("the party"));
    expect(c.textContent).toContain(w.sure("fairly sure"));
    expect(c.textContent).toMatch(/Session of \d{1,2} [A-Z][a-z]+/);
    expect(card("The ferryman plans to sink the barge").textContent).toContain(w.rules["plot"]);
  });

  it("keeps one as a belief, and the list no longer shows it", async () => {
    const api = await open();
    press(card("Mira Voss took the key"), w.place);
    await waitFor(() => expect(screen.getByText(w.keptFor("the party"))).toBeTruthy());
    expect(api.calls).toContain(`answerReading:${REF}:place:r1`);
    await waitFor(() => expect(screen.queryByText("Mira Voss took the key")).toBeNull());
    expect(screen.getByText(w.count(5))).toBeTruthy();
  });

  it("takes a keep back with Undo, and the belief waits again", async () => {
    const api = await open();
    press(card("Mira Voss took the key"), w.place);
    await waitFor(() => expect(screen.getByRole("button", { name: w.undo })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    await waitFor(() => expect(screen.getByText(w.done.undo)).toBeTruthy());
    expect(api.calls).toContain(`answerReading:${REF}:undo:a-r1`);
    await waitFor(() => expect(screen.getByText("Mira Voss took the key")).toBeTruthy());
  });

  it("says no to one, and offers no Undo for that", async () => {
    await open();
    press(card("Roan is the one who warned the harbour"), w.no);
    await waitFor(() => expect(screen.getByText(w.done.no)).toBeTruthy());
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });

  it("holds a belief with a new name back and says what to do first", async () => {
    await open();
    const c = card("Newcomer sent the letter");
    expect(within(c).queryByRole("button", { name: w.place })).toBeNull();
    expect(c.textContent).toContain(w.waitingOn(["Newcomer"]));
    expect(within(c).getByRole("button", { name: w.no })).toBeTruthy();
  });

  it("words a belief that is already gone, and the list then shows the truth", async () => {
    await open();
    press(card("Brannoch burned the ferry"), w.place);
    await waitFor(() => expect(alertText()).toBe(w.errors["gone"]));
    expect(screen.queryByText("Brannoch burned the ferry")).toBeNull();
  });

  it("words a belief DMbot can't write now, and leaves it on the list", async () => {
    await open();
    press(card("Far Hall hid the second key"), w.place);
    await waitFor(() => expect(alertText()).toBe(w.errors["cannot-write"]));
    expect(screen.getByText("Far Hall hid the second key")).toBeTruthy();
  });

  it("shows the wait with its stage while an answer is sent, and sends one answer at a time", async () => {
    const real = mockApi("table");
    let release: () => void = () => {};
    const slow: AccountApi & { calls: string[] } = {
      ...real,
      calls: real.calls,
      answerReading: async (ref, answer, onLook) => {
        onLook?.({ waits: { stage: "queued", waitedSeconds: 3, lastMovedAt: 1, stalled: false } });
        await new Promise<void>((r) => (release = r));
        return real.answerReading!(ref, answer, onLook);
      },
    };
    await open(slow);
    press(card("Mira Voss took the key"), w.place);
    await screen.findByText(w.stages["queued"] as string);
    // every button waits meanwhile, so nothing is sent twice
    for (const b of screen.getAllByRole("button", { name: w.no })) expect((b as HTMLButtonElement).disabled).toBe(true);
    release();
    await waitFor(() => expect(screen.getByText(w.keptFor("the party"))).toBeTruthy());
    expect(real.calls.filter((c) => c.startsWith("answerReading:"))).toHaveLength(1);
  });

  it("says so when DMbot hasn't moved on an answer for a while", async () => {
    const real = mockApi("table");
    let release: () => void = () => {};
    const stuck: AccountApi & { calls: string[] } = {
      ...real,
      calls: real.calls,
      answerReading: async (ref, answer, onLook) => {
        onLook?.({ waits: { stage: "working", waitedSeconds: 400, lastMovedAt: 1, stalled: true } });
        await new Promise<void>((r) => (release = r));
        return real.answerReading!(ref, answer, onLook);
      },
    };
    await open(stuck);
    press(card("Mira Voss took the key"), w.place);
    await screen.findByText(w.stalledWhat);
    release();
    await waitFor(() => expect(screen.getByText(w.keptFor("the party"))).toBeTruthy());
  });

  it("shows how long it has waited and when it last moved from the server's times, not the page's", async () => {
    const real = mockApi("table");
    let release: () => void = () => {};
    const aged: AccountApi & { calls: string[] } = {
      ...real,
      calls: real.calls,
      answerReading: async (ref, answer, onLook) => {
        // queued at server time 1000; 100 seconds later it was picked up (stage working) at 1090, and now it is 1100
        onLook?.({ waits: { stage: "queued", waitedSeconds: 0, lastMovedAt: 1000, stalled: false } });
        onLook?.({ waits: { stage: "working", waitedSeconds: 100, lastMovedAt: 1090, stalled: false } });
        await new Promise<void>((r) => (release = r));
        return real.answerReading!(ref, answer, onLook);
      },
    };
    await open(aged);
    press(card("Mira Voss took the key"), w.place);
    await screen.findByText(w.stages["working"] as string);
    const text = document.querySelector(".work-progress")?.textContent ?? "";
    expect(text).toContain("Running for 1 min."); // 100 seconds since it was queued
    expect(text).toContain("Last progress 10 sec ago"); // 10 seconds since it was picked up, not "just now"
    release();
    await waitFor(() => expect(screen.getByText(w.keptFor("the party"))).toBeTruthy());
  });

  it("words an Undo that has nothing left to take back", async () => {
    const real = mockApi("table");
    const noUndo: AccountApi & { calls: string[] } = {
      ...real,
      calls: real.calls,
      answerReading: async (ref, answer, onLook) => {
        if (answer.action === "undo") throw new ApiError("gone");
        return real.answerReading!(ref, answer, onLook);
      },
    };
    await open(noUndo);
    press(card("Mira Voss took the key"), w.place);
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    await waitFor(() => expect(alertText()).toBe(w.errors["undo-gone"]));
  });

  it("shows nothing at all to a player or a peek viewer", async () => {
    const api = mockApi("table");
    render(<WebReadings api={api} campaignRef={`${REF}.web-player`} />);
    await waitFor(() => expect(api.calls).toContain(`webReadings:${REF}.web-player`));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryByText(w.lead)).toBeNull();
  });

  it("shows nothing against an API older than the list", async () => {
    const old = mockApi("table");
    delete (old as { webReadings?: unknown }).webReadings;
    render(<WebReadings api={old} campaignRef={REF} />);
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("offers to look again when the list can't be read, without a word of the story", async () => {
    const real = mockApi("table");
    let tries = 0;
    const flaky: AccountApi & { calls: string[] } = {
      ...real,
      calls: real.calls,
      webReadings: async (ref) => {
        if (++tries === 1) throw new Error("offline");
        return real.webReadings!(ref);
      },
    };
    render(<WebReadings api={flaky} campaignRef={REF} />);
    await waitFor(() => expect(alertText()).toBe(w.loadFailed));
    fireEvent.click(screen.getByRole("button", { name: w.reload }));
    await screen.findByText(w.count(6));
  });
});

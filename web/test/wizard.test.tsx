/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The start-up wizard's pages (#3956, behind WIZARD_LIVE): start, one page for each step saved as it is chosen, Back and Next,
// Skip with PLAN's words (only the name is asked), checked documents with the right-to-use box first, the rules step, the finish
// wait with its stages, and the failures' words. Run against the wizard's pretend API (wizard-mock.ts).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Wizard from "../src/account/Wizard";
import { mockWizard } from "../src/account/wizard-mock";
import { wizardWords as w } from "../src/content/wizard-words";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const G = "300000000000000001";
const start = async (api = mockWizard(), guild = G) => {
  render(<Wizard api={api} guild={guild} />);
  fireEvent.click(await screen.findByRole("button", { name: w.start }));
  await screen.findByText(w.step(1, 10));
  return api;
};
const next = async (to: number) => {
  fireEvent.click(screen.getByRole("button", { name: w.next }));
  await screen.findByText(w.step(to, 10));
};

describe("the wizard", () => {
  it("offers a start, then shows step 1 of 10", async () => {
    await start();
    expect(screen.getByRole("heading", { name: w.where.heading })).toBeTruthy();
  });

  it("a plan with no room says so at once, before any step", async () => {
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.full`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.noRoom);
  });

  it("picks up where the person was after a reload", async () => {
    const api = await start();
    await next(2);
    cleanup();
    render(<Wizard api={api} guild={G} />);
    expect(await screen.findByText(w.step(2, 10))).toBeTruthy();
  });

  it("Back goes to the page before, and is off on the first", async () => {
    await start();
    expect((screen.getByRole("button", { name: w.back }) as HTMLButtonElement).disabled).toBe(true);
    await next(2);
    fireEvent.click(screen.getByRole("button", { name: w.back }));
    await screen.findByText(w.step(1, 10));
  });

  it("step 1 says what D&D Beyond keeps behind a sign-in, and takes only a D&D Beyond invite link", async () => {
    const api = await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    expect(await screen.findByText(w.where.limit)).toBeTruthy();
    const box = screen.getByLabelText(w.where.inviteLabel) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "https://example.com/not-it" } });
    fireEvent.blur(box);
    expect((await screen.findByRole("alert")).textContent).toBe(w.where.inviteBad);
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/campaigns/join/1234567" } });
    fireEvent.blur(box);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("set:") && c.endsWith(":invite"))).toBe(true));
  });

  it("a public sheet link adds the character; a private one says to make it public", async () => {
    await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    const box = await screen.findByLabelText(w.where.sheetLabel);
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/characters/1111111" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.sheetAdd }));
    expect(await screen.findByText(w.where.sheetFound("Brannoc Stonebelt"), undefined, { timeout: 3000 })).toBeTruthy();
    fireEvent.input(box, { target: { value: "https://www.dndbeyond.com/characters/2222222/private" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.sheetAdd }));
    expect(await screen.findByText(w.where.sheetPrivate, undefined, { timeout: 3000 })).toBeTruthy();
  });

  it("pasted campaign text that holds no characters says to add them by hand", async () => {
    await start();
    fireEvent.click(screen.getByLabelText(w.where.dndbeyond));
    fireEvent.input(await screen.findByLabelText(w.where.pasteLabel), { target: { value: "nothing useful" } });
    fireEvent.click(screen.getByRole("button", { name: w.where.pasteRead }));
    expect(await screen.findByText(w.where.pasteNone)).toBeTruthy();
  });

  it("Skip uses PLAN's words, asks only for the name, then makes the campaign with the defaults", async () => {
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    expect(await screen.findByText(w.skipSays)).toBeTruthy();
    const yes = screen.getByRole("button", { name: w.skipYes }) as HTMLButtonElement;
    expect(yes.disabled).toBe(true); // (no name yet)
    fireEvent.input(screen.getByLabelText(w.skipName), { target: { value: "The Frostmaiden" } });
    fireEvent.click(yes);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("finish:"))).toBe(true));
    expect(api.calls.some((c) => c.startsWith("skip:"))).toBe(true);
  });

  it("a document needs the right-to-use box first, then is checked and counted", async () => {
    const api = await start();
    await next(2);
    await next(3);
    await next(4);
    const add = screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(screen.getByText(w.docs.rightsNeeded)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(w.docs.rights));
    await waitFor(() => expect((screen.getByRole("button", { name: w.docs.add }) as HTMLButtonElement).disabled).toBe(false));
    const link = screen.getByLabelText(w.docs.linkLabel);
    fireEvent.input(link, { target: { value: "https://example.com/lore" } });
    fireEvent.click(screen.getByRole("button", { name: w.docs.addLink }));
    expect(await screen.findByText("https://example.com/lore")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("docAdd:") && c.endsWith(":lore"))).toBe(true);
  });

  it("the rules step keeps the two sets different and lists the seven books", async () => {
    await start();
    for (let to = 2; to <= 6; to++) await next(to);
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThanOrEqual(7);
    fireEvent.click(screen.getByLabelText(w.rules.legacy));
    expect(await screen.findByText(`${w.rules.fallbackLabel}: ${w.rules.current}`)).toBeTruthy();
  });

  it("the last page makes the campaign and shows the stages, then links to it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "The Frostmaiden" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("finish:"))).toBe(true));
    for (let i = 0; i < 30 && !screen.queryByText(w.finish.done); i++) {
      await vi.advanceTimersByTimeAsync(2100);
    }
    expect(screen.getByText(w.finish.done)).toBeTruthy();
    expect(screen.getByRole("link", { name: w.finish.open }).getAttribute("href")).toContain("cmp-new");
  });

  it("a finish that fails says what happened and offers another try", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockWizard();
    render(<Wizard api={api} guild={`${G}.fail`} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    fireEvent.click(await screen.findByRole("button", { name: w.skip }));
    fireEvent.input(await screen.findByLabelText(w.skipName), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: w.skipYes }));
    for (let i = 0; i < 10 && !screen.queryByText(w.finish.retry); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getAllByText(w.finish.errors["failed"]!).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: w.finish.retry })).toBeTruthy();
  });

  it("throwing the setup away asks once, then starts fresh", async () => {
    const api = await start();
    fireEvent.click(screen.getByRole("button", { name: w.discard }));
    expect(await screen.findByText(w.discardAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.discardYes }));
    await screen.findByRole("button", { name: w.start });
    expect(api.calls.some((c) => c.startsWith("discard:"))).toBe(true);
  });
});

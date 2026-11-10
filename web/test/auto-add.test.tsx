/** @jsxImportSource preact */
// @vitest-environment happy-dom
// DMbot adds what it is sure of, on the website (#3879, behind AUTO_ADD_LIVE; core #3887): the counts notice after an upload, the
// wait while it is applying, links to the Names page opened on that upload and to the Story Graph, Undo this upload with one
// confirmation, the questions left, the setting "Ask me before adding", and the Names filter. Counts and labels only.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import { mockApi } from "../src/account/mock";
import { fromHash } from "../src/account/Names";
import UploadNotice from "../src/account/UploadNotice";
import { autoAddWords as w } from "../src/content/auto-add-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/auto-add-switch", () => ({
  get AUTO_ADD_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = true;
});

const SOURCE = { kind: "lore" as const, id: "up1" };
const show = (campaign: string, source = SOURCE) => {
  const api = mockApi("table");
  render(<UploadNotice api={api} campaignRef={campaign} source={source} />);
  return api;
};

describe("the words", () => {
  it("say what was added, with the kinds, and leave out what is zero", () => {
    expect(w.added(62, { npc: 30, place: 24 }, 48, "Lore, 24 Oct")).toBe("Added 62 names (30 NPCs and 24 places) and 48 connections from Lore, 24 Oct.");
    expect(w.added(1, {}, 0, "Lore")).toBe("Added 1 name from Lore.");
    expect(w.added(0, {}, 1, "")).toBe("Added 1 connection.");
    expect(w.added(0, {}, 0, "Lore")).toBe("DMbot was not sure of anything to add from Lore.");
  });
});

describe("the notice", () => {
  it("shows the counts, the links and the questions left, and never a name", async () => {
    show("100.cmp1.autoadd");
    expect((await screen.findByRole("status")).textContent).toContain("Added 62 names");
    expect(screen.getByRole("status").textContent).toContain("48 connections from Lore, 24 Oct.");
    expect((screen.getByRole("link", { name: w.seeNames }) as HTMLAnchorElement).getAttribute("href")).toBe("/account/names/100.cmp1.autoadd#from=up1");
    expect((screen.getByRole("link", { name: w.seeGraph }) as HTMLAnchorElement).getAttribute("href")).toBe("/account/web/100.cmp1.autoadd");
    expect(screen.getByText(w.questions(3), { exact: false })).toBeTruthy();
    expect(screen.getByRole("link", { name: w.questionsLink }).getAttribute("href")).toBe("/account/questions");
  });
  it("shows the wait while DMbot is still adding, then the notice", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    show("100.cmp1.autoadd-applying");
    expect(await screen.findByText(w.applying)).toBeTruthy();
    await act(async () => void (await vi.advanceTimersByTimeAsync(6000)));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Added 62 names"));
  });
  it("shows nothing when nothing was added by itself", async () => {
    const api = show("100.cmp1.autoadd-none");
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("uploadNotice"))).toBe(true));
    await act(async () => void (await new Promise((r) => setTimeout(r, 30))));
    expect(screen.queryByRole("link", { name: w.seeNames })).toBeNull();
  });
  it("says so, once, when the read fails", async () => {
    show("100.cmp1.autoadd-fail");
    expect(await screen.findByText(w.notice.failed)).toBeTruthy();
  });
});

describe("Undo this upload", () => {
  it("asks once, then undoes and says how much was taken back", async () => {
    const api = show("100.cmp1.autoadd");
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(screen.getByText(w.undoAsk)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("undoUpload"))).toBe(false); // (nothing is sent before the confirmation)
    fireEvent.click(screen.getByRole("button", { name: w.undoYes }));
    expect(await screen.findByText(w.tookBack(60, 110))).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("undoUpload"))).toHaveLength(1);
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });
  it("keeps the upload when the answer is No", async () => {
    const api = show("100.cmp1.autoadd");
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    fireEvent.click(screen.getByRole("button", { name: w.undoNo }));
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("undoUpload"))).toBe(false);
  });
  it("offers no Undo after the 7 days", async () => {
    show("100.cmp1.autoadd-old");
    await screen.findByRole("link", { name: w.seeNames });
    expect(screen.queryByRole("button", { name: w.undo })).toBeNull();
  });
});

describe("the setting", () => {
  it("shows Ask me before adding with the switch on, and sends the choice", async () => {
    const api = mockApi("table");
    const page = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    expect(await screen.findByText(w.setting.off)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.setting.toggleOn }));
    await waitFor(() => expect(api.calls.some((c) => c.includes("ask_before_adding"))).toBe(true), { timeout: 5000 });
  });
  it("shows today's page with the switch off", async () => {
    live.on = false;
    const api = mockApi("table");
    const page = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByText(w.setting.heading)).toBeNull();
  });
});

describe("the Names filter", () => {
  it("reads one source id from the fragment, and nothing odd", () => {
    expect(fromHash("#from=up1")).toBe("up1");
    expect(fromHash("#from=a-b_c9")).toBe("a-b_c9");
    for (const bad of ["", "from=up1", "#from=", "#from=a b", "#from=" + "x".repeat(70), "#said=Mira"]) expect(fromHash(bad), bad).toBe("");
  });
});

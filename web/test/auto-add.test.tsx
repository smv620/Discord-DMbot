/** @jsxImportSource preact */
// @vitest-environment happy-dom
// DMbot adds what it is sure of, on the website (#3879, behind AUTO_ADD_LIVE; core #3887): the counts notice after an upload, the
// wait while it is applying, links to the Names page opened on that upload and to the Story Graph, Undo this upload with one
// confirmation, the questions left, the setting "Ask me before adding", and the Names filter. Counts and labels only.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
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

const SOURCE = { kind: "lore" as const, id: "ab".repeat(16) }; // (a website lore upload: 32 hex)
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
    expect((screen.getByRole("link", { name: w.seeNames }) as HTMLAnchorElement).getAttribute("href")).toBe(`/account/names/100.cmp1.autoadd#from=u${SOURCE.id}`);
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
  const failWith = async (error: ApiError): Promise<void> => {
    const api = mockApi("table");
    api.undoUpload = async () => {
      throw error;
    };
    render(<UploadNotice api={api} campaignRef="100.cmp1.autoadd" source={SOURCE} />);
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    fireEvent.click(screen.getByRole("button", { name: w.undoYes }));
  };
  it("says an undo that failed is an undo that failed, and lets the DM try again", async () => {
    await failWith(new ApiError("server"));
    expect((await screen.findByRole("alert")).textContent).toBe(w.undoFailed);
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });
  it("says it is still working when the wait ran out", async () => {
    await failWith(new ApiError("still-waiting"));
    expect((await screen.findByRole("alert")).textContent).toBe(w.undoStillWorking);
  });
  it("says why when it was already undone or is too old", async () => {
    await failWith(new ApiError("undo-done"));
    expect((await screen.findByRole("alert")).textContent).toBe(w.undoneAlready);
    cleanup();
    await failWith(new ApiError("undo-late"));
    expect((await screen.findByRole("alert")).textContent).toBe(w.undoLate);
  });
  it("shows the shared wait panel while it undoes", async () => {
    const api = mockApi("table");
    let finish: (v: { took: number }) => void = () => {};
    api.undoUpload = () => new Promise((r) => (finish = r));
    render(<UploadNotice api={api} campaignRef="100.cmp1.autoadd" source={SOURCE} />);
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    fireEvent.click(screen.getByRole("button", { name: w.undoYes }));
    expect(await screen.findByText(new RegExp(w.undoing))).toBeTruthy();
    finish({ took: 4 });
    expect(await screen.findByText(w.tookBack(4))).toBeTruthy();
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

describe("the notice's Names link", () => {
  const hrefFor = async (source: { kind: "lore" | "import"; id: string }) => {
    render(<UploadNotice api={mockApi("table")} campaignRef="100.cmp1.autoadd" source={source} />);
    return (await screen.findByRole("link", { name: w.seeNames })).getAttribute("href");
  };
  it("opens on the lore upload's own names, asked for by its website id", async () => {
    const id = "ab".repeat(16);
    expect(await hrefFor({ kind: "lore", id })).toBe(`/account/names/100.cmp1.autoadd#from=u${id}`);
  });
  it("opens the whole page for an import, whose names the filter cannot pick yet, and says so", async () => {
    render(<UploadNotice api={mockApi("table")} campaignRef="100.cmp1.autoadd" source={{ kind: "import", id: "ab".repeat(16) }} />);
    const link = await screen.findByRole("link", { name: w.seeAllNames });
    expect(link.getAttribute("href")).toBe("/account/names/100.cmp1.autoadd");
    expect(screen.queryByRole("link", { name: w.seeNames })).toBeNull();
  });
});

describe("the Names filter", () => {
  it("reads one source id from the fragment, and nothing odd", () => {
    expect(fromHash("#from=up1")).toBe("up1");
    expect(fromHash("#from=a-b_c9")).toBe("a-b_c9");
    for (const bad of ["", "from=up1", "#from=", "#from=a b", "#from=" + "x".repeat(70), "#said=Mira"]) expect(fromHash(bad), bad).toBe("");
  });
});

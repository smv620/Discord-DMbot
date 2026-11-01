/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Keep a copy" on a campaign's page (#3148): a Download a backup button of its own, for a campaign's DMs on a paid
// plan, with the same progress and the same failures as the backup inside "Delete this campaign".
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import CampaignPageView from "../src/account/CampaignPage";
import { mockApi } from "../src/account/mock";
import { campaignBackupWords as w } from "../src/content/campaign-backup";
import { campaignDeleteWords as dw } from "../src/content/campaign-delete";

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

const DM = "/account/campaigns/100.cmp1";
const PLAYER = "/account/campaigns/100.cmp1.cp-player";
const section = () => within(screen.getByRole("region", { name: w.heading }));
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

const open = async (path: string, api = mockApi("table")) => {
  render(
    <div class="account">
      <h1>Campaign</h1>
      <CampaignPageView api={api} path={path} />
    </div>,
  );
  await screen.findByRole("heading", { level: 2, name: "Story" });
  return api;
};

describe("who is offered it", () => {
  it("shows a DM on a paid plan Keep a copy and the button, and asks nothing yet", async () => {
    const api = await open(DM);
    expect(await screen.findByRole("heading", { level: 2, name: w.heading })).toBeTruthy();
    expect(screen.getByText(w.lead)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.getByText(w.note)).toBeTruthy(); // the file holds the DM's secrets: players are asked not to open it
    expect(calls(api, "campaignBackup")).toHaveLength(0);
  });

  it("shows nothing on Try It, which has no backup", async () => {
    await open(DM, mockApi("try-it"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Delete this campaign/ })).toBeTruthy());
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("shows a player the button too: a copy is for anyone in the server", async () => {
    await open(PLAYER);
    expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
    expect(screen.getByText(w.note)).toBeTruthy(); // players are asked not to open the file
  });

  it("shows nothing for a role the API did not name", async () => {
    const api = mockApi("table");
    const read = api.campaignPage!;
    api.campaignPage = async (r) => ({ ...(await read(r)), role: "viewer" as never });
    await open(DM, api);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });
});

describe("pressing it", () => {
  it("makes the backup, saves its files and says where to look", async () => {
    const api = await open(DM);
    fireEvent.click(await screen.findByRole("button", { name: w.button }));
    await waitFor(() => expect(calls(api, "campaignBackup")).toEqual(["campaignBackup:100.cmp1"]));
    const done = await section().findByRole("status");
    expect(done.textContent).toContain("Backup saved");
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(calls(api, "deleteCampaign")).toHaveLength(0); // a backup deletes nothing
  });

  it("says what went wrong, in the same words as Delete, and keeps the button", async () => {
    const api = mockApi("table");
    api.campaignBackup = async () => {
      throw new ApiError("too-soon");
    };
    await open(DM, api);
    fireEvent.click(await screen.findByRole("button", { name: w.button }));
    const problem = await section().findByRole("alert");
    expect(problem.textContent).toBe(w.problem["too-soon"]);
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
  });

  it("never talks about deleting, or a button that is not on this page", async () => {
    // every message this page can show: none mentions deleting or "Download a backup first"
    const said = [...Object.values(w.problem), w.done(["a.zip"]), w.done(["a.zip", "b.zip"]), w.bigFile, w.fileSlow, w.note, w.lead];
    for (const text of said) {
      expect(text).not.toMatch(/delet/i);
      expect(text).not.toMatch(/backup first/i);
    }
    // and the Delete box keeps its own words, which do
    expect(dw.problem["backup-failed"]).toMatch(/delet/i);
  });

  it("makes one backup for two quick presses", async () => {
    const api = await open(DM);
    const button = await screen.findByRole("button", { name: w.button });
    fireEvent.click(button);
    fireEvent.click(button);
    await section().findByRole("status");
    expect(calls(api, "campaignBackup")).toHaveLength(1);
  });
});

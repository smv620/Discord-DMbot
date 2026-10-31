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
    expect(screen.getByText(dw.backupNote)).toBeTruthy(); // the same warning as in Delete
    expect(calls(api, "campaignBackup")).toHaveLength(0);
  });

  it("shows nothing on Try It, which has no backup", async () => {
    await open(DM, mockApi("try-it"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Delete this campaign/ })).toBeTruthy());
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });

  it("shows a player nothing", async () => {
    await open(PLAYER);
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
    expect(problem.textContent).toBe(dw.problem["too-soon"]);
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
  });
});

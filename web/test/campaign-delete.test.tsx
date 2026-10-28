/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Delete this campaign" and the campaigns of a server DMbot can't reach, on My Account (#2555; core #2554).
// Shown only while CAMPAIGN_DELETE_LIVE is true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import Account from "../src/account/Account";
import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { campaignDeleteWords as w } from "../src/content/campaign-delete";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/campaign-delete", async (original) => {
  const real = await original<typeof import("../src/content/campaign-delete")>();
  return {
    ...real,
    get CAMPAIGN_DELETE_LIVE() {
      return live.on;
    },
  };
});

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  live.on = false;
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

async function page(api: AccountApi = mockApi("lost-server")) {
  live.on = true;
  render(<Account api={api} go={vi.fn()} search="" />);
  await screen.findByRole("link", { name: "The Brynwater Crossing" });
  return api;
}
const card = (name: string) => screen.getByRole("link", { name }).closest("li") as HTMLElement;
const open = async (name: string) => {
  const row = card(name);
  fireEvent.click(within(row).getByRole("button", { name: w.buttonFor(name) }));
  return within(row).getByRole("group", { name: w.heading(name) });
};

describe("while it is switched off", () => {
  it("shows no Delete button and no note, and asks nothing", async () => {
    const api = mockApi("lost-server");
    render(<Account api={api} go={vi.fn()} search="" />);
    await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(screen.queryByRole("button", { name: /Delete this campaign/ })).toBeNull();
    expect(screen.queryByText(w.unreachable)).toBeNull();
    expect(screen.queryByRole("link", { name: "Lost Hollow" })).toBeNull(); // a card on an unreachable server stays hidden
    expect(calls(api, "deleteCampaign")).toHaveLength(0);
    expect(calls(api, "campaignBackup")).toHaveLength(0);
  });
});

describe("the list", () => {
  it("shows a campaign from a server DMbot can't reach, with its note", async () => {
    await page();
    const lost = card("Lost Hollow");
    expect(within(lost).getByText(w.unknownServer)).toBeTruthy();
    expect(within(lost).getByText(w.unreachable)).toBeTruthy();
    expect(within(card("The Brynwater Crossing")).queryByText(w.unreachable)).toBeNull();
  });

  it("offers Delete by the API's canDelete when it sends one (a campaign with no owner yet)", async () => {
    const api = mockApi("lost-server");
    const base = api.me.bind(api);
    api.me = async () => {
      const got = await base();
      return got && { ...got, campaigns: got.campaigns.map((c) => (c.id === "cmp-varrow" ? { ...c, canDelete: true } : c.id === "cmp-ashen" ? { ...c, canDelete: false } : c)) };
    };
    await page(api);
    expect(within(card("Shrine of Varrow")).getByRole("button", { name: w.buttonFor("Shrine of Varrow") })).toBeTruthy();
    expect(within(card("Ashen Crown")).queryByRole("button", { name: /Delete this campaign/ })).toBeNull();
  });

  it("offers Delete to the owner only, never to a co-DM or a player", async () => {
    await page();
    expect(within(card("The Brynwater Crossing")).getByRole("button", { name: w.buttonFor("The Brynwater Crossing") })).toBeTruthy();
    expect(within(card("Lost Hollow")).getByRole("button", { name: w.buttonFor("Lost Hollow") })).toBeTruthy();
    expect(within(card("Shrine of Varrow")).queryByRole("button", { name: /Delete this campaign/ })).toBeNull();
    expect(within(card("The Lantern Road")).queryByRole("button", { name: /Delete this campaign/ })).toBeNull();
  });
});

describe("deleting", () => {
  it("says what goes and that it can't be undone, offers the backup first, and needs the typed name", async () => {
    const api = await page();
    const box = await open("The Brynwater Crossing");
    expect(within(box).getByText(w.warning("The Brynwater Crossing"))).toBeTruthy();
    const buttons = within(box).getAllByRole("button");
    expect(buttons[0]!.textContent).toBe(w.backup); // the backup is the first button
    const confirm = within(box).getByRole("button", { name: w.confirm }) as HTMLButtonElement;
    expect(buttons.indexOf(confirm)).toBeGreaterThan(0);
    expect(confirm.disabled).toBe(true);
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "the brynwater" } });
    expect(confirm.disabled).toBe(true); // part of the name is not the name
    expect(within(box).getByText(w.noMatch)).toBeTruthy(); // and the page says why the button is off
    fireEvent.click(confirm);
    expect(calls(api, "deleteCampaign")).toHaveLength(0);
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "  the BRYNWATER   crossing " } });
    expect(confirm.disabled).toBe(false); // any case, extra spaces ignored, as in Discord
  });

  it("deletes it, says so, and the campaign leaves the list", async () => {
    const api = await page();
    const box = await open("The Brynwater Crossing");
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "The Brynwater Crossing" } });
    fireEvent.click(within(box).getByRole("button", { name: w.confirm }));
    expect(await screen.findByText(w.deleted("The Brynwater Crossing"))).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("link", { name: "The Brynwater Crossing" })).toBeNull());
    expect(calls(api, "deleteCampaign")).toEqual(["deleteCampaign:100.cmp1:The Brynwater Crossing"]);
    expect(screen.queryByText(w.channelsLeft)).toBeNull(); // DMbot could reach the server
  });

  it("on a server DMbot can't reach, says its Discord channels stay", async () => {
    await page();
    const box = await open("Lost Hollow");
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "Lost Hollow" } });
    fireEvent.click(within(box).getByRole("button", { name: w.confirm }));
    expect(await screen.findByText(new RegExp(w.channelsLeft))).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("link", { name: "Lost Hollow" })).toBeNull());
  });

  it("Keep it closes the box and deletes nothing", async () => {
    const api = await page();
    const box = await open("The Brynwater Crossing");
    fireEvent.click(within(box).getByRole("button", { name: w.keep }));
    expect(screen.queryByRole("group", { name: w.heading("The Brynwater Crossing") })).toBeNull();
    expect(screen.getByText(w.kept("The Brynwater Crossing"))).toBeTruthy();
    expect(calls(api, "deleteCampaign")).toHaveLength(0);
  });

  it.each([
    ["not-allowed", "not-allowed"],
    ["session-running", "session-running"],
    ["name-mismatch", "name-mismatch"],
    ["server", "server"],
    ["network", "network"],
  ])("a refusal (%s) has its own words and leaves the campaign", async (kind) => {
    const api = mockApi("lost-server");
    api.deleteCampaign = async () => {
      throw new ApiError(kind as never);
    };
    await page(api);
    const box = await open("The Brynwater Crossing");
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "The Brynwater Crossing" } });
    fireEvent.click(within(box).getByRole("button", { name: w.confirm }));
    expect(await within(box).findByText(w.problem[kind]!)).toBeTruthy();
    expect(screen.getByRole("link", { name: "The Brynwater Crossing" })).toBeTruthy();
  });

  it("the pretend API refuses while a session is running, and a wrong name", async () => {
    const api = mockApi("lost-server");
    await page(api);
    await expect(api.deleteCampaign!("100.cmp1", "Wrong")).rejects.toMatchObject({ kind: "name-mismatch" });
    await expect(api.deleteCampaign!("100.cmp3.cp-co", "Shrine of Varrow")).rejects.toMatchObject({ kind: "not-allowed" });
  });

  it("a campaign that is already gone leaves the list, and is never called deleted", async () => {
    const api = mockApi("lost-server");
    const real = api.deleteCampaign!;
    api.deleteCampaign = async (ref, name) => {
      await real(ref, name); // (it goes, then the second try finds nothing)
      throw new ApiError("not-found");
    };
    await page(api);
    const box = await open("The Brynwater Crossing");
    fireEvent.input(within(box).getByLabelText(w.typeName), { target: { value: "The Brynwater Crossing" } });
    fireEvent.click(within(box).getByRole("button", { name: w.confirm }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "The Brynwater Crossing" })).toBeNull());
    expect(screen.getByText(w.problem["not-found"]!)).toBeTruthy();
    expect(screen.queryByText(w.deleted("The Brynwater Crossing"))).toBeNull();
  });
});

describe("the backup", () => {
  it("saves a file from the backup button, before anything is deleted", async () => {
    const api = await page();
    const box = await open("The Brynwater Crossing");
    fireEvent.click(within(box).getByRole("button", { name: w.backup }));
    expect(await within(box).findByText(w.backupDone(["pretend-campaign-2026-10-27.dmbot.json.gz"]))).toBeTruthy();
    expect(calls(api, "campaignBackup")).toEqual(["campaignBackup:100.cmp1"]);
    expect(calls(api, "deleteCampaign")).toHaveLength(0);
  });

  it("shows the stage and how long it has run while the backup is made", async () => {
    const api = await page();
    const box = await open("The Brynwater Crossing");
    let finish: (set: { files: { name: string; blob: Blob }[] }) => void = () => {};
    api.campaignBackup = (_ref, onProgress) => {
      const now = Math.floor(Date.now() / 1000);
      onProgress?.({ stage: "reading", state: "running", startedAt: now - 20, updatedAt: now - 8, serverNow: now });
      return new Promise((resolve) => (finish = resolve));
    };
    fireEvent.click(within(box).getByRole("button", { name: w.backup }));
    // DMbot's own words: the stage it is at, how long it has run, and when it last moved.
    expect(await within(box).findByText(w.stage["reading"] as string)).toBeTruthy();
    expect(within(box).getByText(/Running for/)).toBeTruthy();
    expect(within(box).getByText(/Last progress/)).toBeTruthy();
    finish({ files: [{ name: "a.dmbot.json.gz", blob: new Blob(["{}"]) }] });
    await waitFor(() => expect(within(box).queryByText(/Running for/)).toBeNull());
  });

  it("no longer says the transcripts are missing, now that the backup carries them", async () => {
    await page();
    const box = await open("The Brynwater Crossing");
    expect(within(box).queryByText(new RegExp(w.backupWithoutTranscripts))).toBeNull();
  });

  describe("a backup of several files", () => {
    const saved: string[] = [];
    beforeEach(() => {
      saved.length = 0;
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        saved.push(this.download);
      });
    });
    afterEach(() => vi.restoreAllMocks());

    it("saves the main file and every transcripts file, and says to keep them together", async () => {
      const files = [
        "pretend-campaign-2026-10-27.dmbot.json.gz",
        "pretend-campaign-2026-10-27.transcripts-1-of-2.dmbot.json.gz",
        "pretend-campaign-2026-10-27.transcripts-2-of-2.dmbot.json.gz",
      ];
      const api = mockApi("table");
      api.campaignBackup = (ref, onProgress, onFile) => mockApi("table").campaignBackup!(`${ref}.bk-parts`, onProgress, onFile);
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(w.backupDone(files), {}, { timeout: 5000 })).toBeTruthy();
      expect(saved).toEqual(files);
      expect(within(box).getByText(/Backup saved: 3 files\. Keep them all together/)).toBeTruthy();
      for (const file of files) expect(within(box).getByText(file, { selector: "li" })).toBeTruthy(); // one to a line, easy to read on a phone
      expect(within(box).queryByText(w.backupBigFile)).toBeNull(); // (none of these files is large)
    });

    it("says a file is large when any file, the main one included, is over 10 MB", async () => {
      const api = mockApi("table");
      const big = new Blob([new Uint8Array(w.bigFileBytes + 1)]);
      api.campaignBackup = async () => ({ files: [{ name: "x.dmbot.json.gz", blob: big }] });
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(w.backupBigFile, {}, { timeout: 5000 })).toBeTruthy();
    });

    it("shows the clock from the request, and a stuck file as stalled with what to do", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const api = mockApi("table");
      api.campaignBackup = (_ref, _progress, onFile) => {
        onFile?.(1, 3); // one of three files fetched, then nothing
        return new Promise(() => {});
      };
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(`1 of 3 ${w.filesUnit(3)}`)).toBeTruthy();
      expect(within(box).queryByText(w.fileSlow)).toBeNull();
      await vi.advanceTimersByTimeAsync(90_000);
      expect(within(box).getByText(w.fileSlow)).toBeTruthy();
      vi.useRealTimers();
    });

    it("shows how many files are fetched while it goes", async () => {
      const api = mockApi("table");
      let hold: () => void = () => {};
      api.campaignBackup = async (_ref, _progress, onFile) => {
        onFile?.(2, 7);
        await new Promise<void>((resolve) => (hold = resolve));
        return { files: [{ name: "a.dmbot.json.gz", blob: new Blob(["{}"]) }] };
      };
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(w.stage["fetching"] as string)).toBeTruthy();
      expect(within(box).getByText(`2 of 7 ${w.filesUnit(7)}`)).toBeTruthy();
      hold();
    });

    it("says the whole backup failed when one file does not come through, and saves nothing", async () => {
      const api = mockApi("table");
      api.campaignBackup = (ref, p, f) => mockApi("table").campaignBackup!(`${ref}.bk-partfail`, p, f);
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(w.problem["backup-part"] as string)).toBeTruthy();
      expect(saved).toEqual([]);
    });

    it("says what to change when the campaign has more transcripts files than one backup carries", async () => {
      const api = mockApi("table");
      api.campaignBackup = (ref, p, f) => mockApi("table").campaignBackup!(`${ref}.bk-toomany`, p, f);
      await page(api);
      const box = await open("The Brynwater Crossing");
      fireEvent.click(within(box).getByRole("button", { name: w.backup }));
      expect(await within(box).findByText(w.problem["backup-too-many-files"] as string)).toBeTruthy();
      expect(w.problem["backup-too-many-files"]).toMatch(/Keep transcripts for/);
    });
  });

  it("a Try It campaign is offered no backup, but can still be deleted", async () => {
    await page(mockApi("try-it"));
    const box = await open("The Brynwater Crossing");
    expect(within(box).queryByRole("button", { name: w.backup })).toBeNull();
    expect(within(box).getByRole("button", { name: w.confirm })).toBeTruthy();
  });
});

describe("the real client", () => {
  it("posts the typed name to the campaign's own address", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname} ${init?.body ?? ""}`);
      return new Response(JSON.stringify({ status: "deleted", channelsLeft: true }), { status: 200 });
    }) as typeof fetch);
    expect(await api.deleteCampaign!("100.cmp1", "Brynwater")).toEqual({ status: "deleted", channelsLeft: true });
    expect(seen).toEqual(['POST /me/campaigns/100.cmp1/delete {"name":"Brynwater"}']);
  });

  it.each([
    [403, "not_the_owner", "not-allowed"],
    [404, "not_found", "not-found"],
    [409, "session_running", "session-running"],
    [422, "name_mismatch", "name-mismatch"],
    [403, "no_backup_on_this_plan", "no-backup"],
  ])("reads %s %s as %s, and each has words", async (status, code, kind) => {
    const api = httpApi("https://api.example", (async () => new Response(JSON.stringify({ error: code }), { status })) as typeof fetch);
    await expect(api.deleteCampaign!("100.cmp1", "x")).rejects.toMatchObject({ kind });
    expect(w.problem[kind]).toBeTruthy();
  });
});

describe("held", () => {
  it("is held with its What's new entry, one Help step and one GO_LIVE row", async () => {
    const real = await vi.importActual<typeof import("../src/content/campaign-delete")>("../src/content/campaign-delete");
    const { held } = await import("../src/content/whats-new");
    expect(held.map((h) => h.title).includes("Delete a campaign from the website")).toBe(!real.CAMPAIGN_DELETE_LIVE);
    const { featureSteps } = await import("../src/content/help-features");
    expect(featureSteps.filter((f) => f.whatsNew === "Delete a campaign from the website")).toHaveLength(1);
    const golive = readFileSync(process.cwd() + "/../docs/GO_LIVE.md", "utf8");
    expect(golive.split("\n").filter((l) => l.startsWith("| `CAMPAIGN_DELETE_LIVE`"))).toHaveLength(1);
  });

  it("has nothing serious or critical for axe", async () => {
    await page();
    await open("The Brynwater Crossing");
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

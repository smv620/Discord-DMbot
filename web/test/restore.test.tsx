/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Load a backup page (#2745): pick the copy, say new or replace, send its transcripts files (all or none),
// see what will happen, press Load it, watch DMbot work, and what is shown at the end. Shown only while
// WEB_RESTORE_LIVE is true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type Me } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Restore from "../src/account/Restore";
import { restoreWords as w } from "../src/content/restore-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/restore-switch", async (original) => {
  const real = await original<typeof import("../src/content/restore-switch")>();
  return {
    ...real,
    get WEB_RESTORE_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const SERVER = "200000000000000001";
const FILE = new RegExp(`^${w.fileLabel}`); // (the other labels say "the copy" too)
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));
const gz = (name: string): File => new File([new Uint8Array([31, 139, 8])], name, { type: "application/gzip" });

/** The pretend API, with a campaign of the person's own in the server so a copy can replace it. */
function withOwnCampaign(): AccountApi & { calls: string[] } {
  const api = mockApi("table");
  const real = api.me.bind(api);
  api.me = async (): Promise<Me | null> => {
    const me = await real();
    if (!me) return me;
    return {
      ...me,
      campaigns: [
        ...me.campaigns,
        { id: "cmp-own", ref: `${SERVER}.own`, name: "The Silver Lock", serverName: "Thursday Table", lastPlayedAt: null, status: "active", role: "owner" },
      ],
    };
  };
  return api;
}

async function page(api: AccountApi = mockApi("table")) {
  live.on = true;
  render(<Restore api={api} />);
  await screen.findByLabelText(FILE);
  return api;
}
const pick = (label: string | RegExp, ...files: File[]) => {
  const input = screen.getByLabelText(label) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
};
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("while it is switched off", () => {
  it("says so and asks nothing", async () => {
    const api = mockApi("table");
    render(<Restore api={api} />);
    expect(await screen.findByText(w.unavailable)).toBeTruthy();
    expect(calls(api, "restore")).toHaveLength(0);
  });
});

describe("a copy with no transcripts files", () => {
  it("is checked, confirmed and only then sent", async () => {
    const api = await page();
    pick(FILE, gz("Lost World.dmbot.json.gz"));
    press(w.checkFile);
    expect(await screen.findByRole("heading", { name: w.confirmHeading })).toBeTruthy();
    expect(screen.getByText(w.confirmNew("Lost World"))).toBeTruthy();
    expect(calls(api, "restoreGo")).toHaveLength(0); // nothing has gone to DMbot yet
    press(w.yes);
    await waitFor(() => expect(calls(api, "restoreGo")).toEqual([`restoreGo:${SERVER}:rs1:with`]));
    expect(calls(api, "restoreStage")).toEqual([`restoreStage:${SERVER}:new`]);
  });

  it("cancelling takes it back and shows the form again", async () => {
    const api = await page();
    pick(FILE, gz("Lost World.dmbot.json.gz"));
    press(w.checkFile);
    await screen.findByRole("heading", { name: w.confirmHeading });
    press(w.cancel);
    await screen.findByLabelText(FILE);
    expect(calls(api, "restoreCancel")).toHaveLength(1);
    expect(calls(api, "restoreGo")).toHaveLength(0);
  });

  it("asks for the file first", async () => {
    const api = await page();
    press(w.checkFile);
    expect((await screen.findByRole("alert")).textContent).toBe(w.pickFirst);
    expect(calls(api, "restoreStage")).toHaveLength(0);
  });

  it("says plainly when the file is not a backup", async () => {
    await page();
    pick(FILE, gz("bad.txt"));
    press(w.checkFile);
    expect((await screen.findByRole("alert")).textContent).toBe(w.problems["not-a-backup"]);
    expect(screen.queryByRole("heading", { name: w.confirmHeading })).toBeNull();
  });
});

describe("a copy with transcripts files", () => {
  const stage = async () => {
    const api = await page();
    pick(FILE, gz("Lost World-parts-2.dmbot.json.gz"));
    press(w.checkFile);
    await screen.findByText(w.partsText(2));
    return api;
  };

  it("asks for all of them, and refuses a wrong count without sending any", async () => {
    const api = await stage();
    pick(new RegExp(`^${w.partsLabel}`), gz("a.gz"));
    expect(screen.getByText(w.partsPicked(1, 2))).toBeTruthy();
    press(w.sendParts);
    expect((await screen.findByRole("alert")).textContent).toBe(w.partsWrongCount(1, 2));
    expect(calls(api, "restorePart")).toHaveLength(0);
  });

  it("sends them one by one, then asks to confirm", async () => {
    const api = await stage();
    pick(new RegExp(`^${w.partsLabel}`), gz("a.gz"), gz("b.gz"));
    press(w.sendParts);
    expect(await screen.findByRole("heading", { name: w.confirmHeading })).toBeTruthy();
    expect(calls(api, "restorePart")).toEqual([`restorePart:${SERVER}:rs1:1`, `restorePart:${SERVER}:rs1:2`]);
    press(w.yes);
    await waitFor(() => expect(calls(api, "restoreGo")).toEqual([`restoreGo:${SERVER}:rs1:with`]));
  });

  it("can be brought back without its transcripts, and says so", async () => {
    const api = await stage();
    press(w.withoutParts);
    expect(await screen.findByText(w.noTranscriptsNote)).toBeTruthy();
    press(w.yes);
    await waitFor(() => expect(calls(api, "restoreGo")).toEqual([`restoreGo:${SERVER}:rs1:without`]));
  });
});

describe("replacing a campaign", () => {
  it("names the campaign, warns that it can't be undone and offers a download first", async () => {
    const api = await page(withOwnCampaign());
    fireEvent.click(screen.getByLabelText(w.asReplace));
    pick(FILE, gz("Lost World.dmbot.json.gz"));
    press(w.checkFile);
    await screen.findByRole("heading", { name: w.confirmHeading });
    expect(screen.getByText(w.confirmReplace("Lost World", "The Silver Lock"))).toBeTruthy();
    expect(screen.getByRole("link", { name: w.downloadFirst }).getAttribute("href")).toContain(`${SERVER}.own`);
    expect(calls(api, "restoreStage")).toEqual([`restoreStage:${SERVER}:${SERVER}.own`]);
    expect(screen.getByRole("button", { name: w.yesReplace })).toBeTruthy();
  });

  it("offers no replacing when the person runs nothing in that server", async () => {
    await page();
    expect((screen.getByLabelText(w.asReplace) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(w.noneToReplace)).toBeTruthy();
  });
});

describe("while DMbot works and at the end", () => {
  it("shows the stage, then the finished campaign and the lines left out", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = await page();
      pick(FILE, gz("Lost World-parts-1.dmbot.json.gz"));
      press(w.checkFile);
      await screen.findByText(w.partsText(1));
      pick(new RegExp(`^${w.partsLabel}`), gz("a.gz"));
      press(w.sendParts);
      await screen.findByRole("heading", { name: w.confirmHeading });
      press(w.yes);
      expect((await screen.findAllByText(w.stage["restore_reading"] as string)).length).toBeGreaterThan(0);
      await vi.advanceTimersByTimeAsync(20_000);
      expect(await screen.findByRole("heading", { name: w.doneTitle("Lost World") })).toBeTruthy();
      expect(screen.getByText(w.doneNew)).toBeTruthy();
      expect(screen.getByText(w.leftOut(2))).toBeTruthy();
      expect(screen.getByRole("link", { name: w.openCampaign }).getAttribute("href")).toContain(`${SERVER}.restored`);
      expect(calls(api, "restoreStatus").length).toBeGreaterThan(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says what to do when DMbot could not do it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = mockApi("table");
      const real = api.me.bind(api);
      // A server whose id ends in 9 refuses at the end (the pretend DMbot's "plan" reason).
      api.me = async () => {
        const me = await real();
        return me && { ...me, servers: me.servers.map((s) => (s.hasDmbot ? { ...s, id: `${s.id.slice(0, -1)}9` } : s)) };
      };
      await page(api);
      pick(FILE, gz("Lost World.dmbot.json.gz"));
      press(w.checkFile);
      await screen.findByRole("heading", { name: w.confirmHeading });
      press(w.yes);
      await vi.advanceTimersByTimeAsync(30_000);
      await waitFor(() => expect(screen.getByText(w.reasons["plan"] as string)).toBeTruthy());
      press(w.startOver);
      await screen.findByLabelText(FILE);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("finding its way back", () => {
  it("shows a restore that is already on its way", async () => {
    const api = mockApi("table");
    await api.restoreStage?.(SERVER, gz("Lost World.dmbot.json.gz"));
    live.on = true;
    render(<Restore api={api} />);
    expect(await screen.findByRole("heading", { name: w.confirmHeading })).toBeTruthy();
  });

  it("shows the one on its way when the API says another is pending", async () => {
    const api = mockApi("table");
    const real = api.restoreStatus!.bind(api);
    let first = true;
    api.restoreStatus = async (server, id) => {
      if (first) {
        first = false;
        return null; // nothing was there when the page opened
      }
      return real(server, id);
    };
    await api.restoreStage?.(SERVER, gz("Lost World.dmbot.json.gz")); // another tab started it meanwhile
    await page(api);
    pick(FILE, gz("Other.dmbot.json.gz"));
    press(w.checkFile);
    expect(await screen.findByRole("heading", { name: w.confirmHeading })).toBeTruthy();
    expect(screen.getByText(w.confirmNew("Lost World"))).toBeTruthy();
  });
});

describe("the page itself", () => {
  it("says so when nobody is signed in", async () => {
    live.on = true;
    const api = mockApi("signed-out");
    render(<Restore api={api} />);
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });

  it("says when there is no server with DMbot", async () => {
    live.on = true;
    const api = mockApi("table");
    api.me = async () => {
      const me = await mockApi("table").me();
      return me && { ...me, servers: [] };
    };
    render(<Restore api={api} />);
    expect(await screen.findByText(w.noServers)).toBeTruthy();
  });

  it("refuses with words when the API fails", async () => {
    const api = await page();
    api.restoreStage = async () => {
      throw new ApiError("too-soon");
    };
    pick(FILE, gz("Lost World.dmbot.json.gz"));
    press(w.checkFile);
    expect((await screen.findByRole("alert")).textContent).toBe(w.problems["too-soon"]);
  });

  it("has no serious accessibility problem in its steps", async () => {
    await page();
    const check = async () => {
      const result = await axe.run(document.body, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }, rules: { "color-contrast": { enabled: false } } });
      expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
    };
    await check();
    pick(FILE, gz("Lost World-parts-2.dmbot.json.gz"));
    press(w.checkFile);
    await screen.findByText(w.partsText(2));
    await check();
    press(w.withoutParts);
    await screen.findByRole("heading", { name: w.confirmHeading });
    await check();
    expect(within(document.body).getByRole("button", { name: w.cancel })).toBeTruthy();
  });
});

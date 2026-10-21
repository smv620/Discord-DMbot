/** @jsxImportSource preact */
// @vitest-environment happy-dom
// When the Import sessions page opens (#2260): for the whole site (IMPORT_LIVE), or for one
// campaign whose server the API says has imports on (`importsOn`, #2259: the owner's test
// servers). Only IMPORT_LIVE advertises imports: the Help step, What's new and the Transcripts
// page's link stay hidden when the page is open for a test server alone.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Campaign } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import Transcripts from "../src/account/Transcripts";
import { dmSteps } from "../src/content/help";
import { importPage as w } from "../src/content/import-words";
import { entries } from "../src/content/whats-new";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const here: Campaign = { id: "cmp1", name: "Test table", serverName: "Test server", lastPlayedAt: null, status: "active", role: "owner" };

function opened(campaigns: Campaign[], path = "/account/import/100.cmp1") {
  const api = mockApi("table");
  const real = api.me.bind(api);
  api.me = async () => ({ ...(await real())!, campaigns });
  render(<Import api={api} path={path} />);
  return api;
}

describe("the page", () => {
  it("is closed when the site switch and the server's setting are both off", async () => {
    live.on = false;
    opened([{ ...here, importsOn: false }]);
    expect(await screen.findByText(w.notOpen)).toBeTruthy();
    expect(screen.queryByText(w.pickLabel)).toBeNull();
  });

  it("is closed when the API says nothing about it (an older API)", async () => {
    opened([here]);
    expect(await screen.findByText(w.notOpen)).toBeTruthy();
  });

  it("is closed for a campaign that is not the person's", async () => {
    opened([{ ...here, id: "other", importsOn: true }]);
    expect(await screen.findByText(w.notOpen)).toBeTruthy();
  });

  it("opens for a test server's campaign, with the site switch off", async () => {
    opened([{ ...here, importsOn: true }]);
    expect(await screen.findByLabelText(w.pickLabel, { selector: "input" })).toBeTruthy();
    expect(screen.queryByText(w.notOpen)).toBeNull();
  });

  it("opens for the whole site with the switch on, whatever the server's setting", async () => {
    live.on = true;
    opened([{ ...here, importsOn: false }]);
    expect(await screen.findByLabelText(w.pickLabel, { selector: "input" })).toBeTruthy();
  });

  it("says it is loading while it asks, and closed when the question fails", async () => {
    const api = mockApi("table");
    api.me = () => new Promise(() => undefined);
    render(<Import api={api} path="/account/import/100.cmp1" />);
    expect(screen.getByText(w.loading)).toBeTruthy();
    cleanup();
    const failing = mockApi("table");
    failing.me = async () => {
      throw new Error("down");
    };
    render(<Import api={failing} path="/account/import/100.cmp1" />);
    expect(await screen.findByText(w.notOpen)).toBeTruthy();
  });

  it("shows the sign-in panel to someone who is signed out", async () => {
    const api = mockApi("signed-out");
    render(<Import api={api} path="/account/import/100.cmp1" />);
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });
});

describe("what still waits for IMPORT_LIVE", () => {
  it("keeps the Help step, the What's new entry and the Transcripts link hidden when only a test server has imports on", async () => {
    live.on = false;
    expect(dmSteps.map((s) => s.text).join(" ")).not.toContain("press Import sessions");
    expect(entries.some((e) => e.title === "Bring in older sessions")).toBe(false);
    const link = () => screen.queryByRole("link", { name: w.heading });
    render(<Transcripts api={mockApi("table")} path="/account/transcripts/200000000000000001.cmp1" />);
    await screen.findByRole("heading", { name: "The Brynwater Crossing" });
    expect(link()).toBeNull();
    // The same page with the site switch on does link it (so the check above can fail).
    cleanup();
    live.on = true;
    render(<Transcripts api={mockApi("table")} path="/account/transcripts/200000000000000001.cmp1" />);
    await screen.findByRole("heading", { name: "The Brynwater Crossing" });
    expect(link()).toBeTruthy();
  });
});

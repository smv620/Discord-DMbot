/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The recap ticks on the Import page appear only when the API says a recap can be written
// (`recapsPossible`, #2227/#2229), and say why not when the DM can do something about it. The
// reply that carries it is the preview's (and the start's); the page never offers a tick the bot
// would refuse, and sends no `writeRecap` when none is offered.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportStarted } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import { importPage as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ on: false, recaps: true }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.on;
    },
  };
});
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.recaps;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
  live.recaps = true;
});

const s1 = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", { type: "text/plain" });

/** Opens the page for the campaign `ref` and goes as far as the "Ready?" step. */
async function ready(ref: string, start?: (api: ReturnType<typeof mockApi>) => void) {
  const api = mockApi("table");
  start?.(api);
  live.on = true;
  render(<Import api={api} path={`/account/import/${ref}`} />);
  const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [s1], configurable: true });
  fireEvent.change(input);
  fireEvent.click(screen.getByRole("button", { name: w.preview }));
  fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
  await screen.findByText(/^Checked:/);
  return api;
}

const tick = () => screen.queryByLabelText(w.recapsOne("session-1-2026-09-14.txt"));

describe("whether the ticks are offered", () => {
  it("offers them when a recap can be written", async () => {
    await ready("100.cmp1");
    expect(tick()).toBeTruthy();
    expect(screen.queryByText(w.recapsWhy.campaign_off ?? "")).toBeNull();
  });

  it("offers none and says why when the DM turned recaps off for the campaign", async () => {
    await ready("100.recaps-campaign_off");
    expect(tick()).toBeNull();
    expect(screen.getByText(w.recapsWhy.campaign_off ?? "")).toBeTruthy();
  });

  it("offers none and links the plans when the plan has no recaps", async () => {
    await ready("100.recaps-plan");
    expect(tick()).toBeNull();
    const note = screen.getByText(new RegExp(w.recapsWhy.plan ?? ""));
    expect(note.querySelector("a")?.getAttribute("href")).toBe("/pricing");
  });

  it("offers none and says nothing when only the whole server has recaps off", async () => {
    await ready("100.recaps-off");
    expect(tick()).toBeNull();
    expect(screen.queryByText(w.recapsWhy.campaign_off ?? "")).toBeNull();
    expect(screen.queryByText(new RegExp(w.recapsWhy.plan ?? ""))).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("offers none and says nothing when the API could not tell", async () => {
    await ready("100.recaps-unknown");
    expect(tick()).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("reads an API that sends no answer as 'no' (never offers a tick the bot may refuse)", async () => {
    await ready("100.cmp1", (api) => {
      const real = api.importPreview!.bind(api);
      api.importPreview = async (...a) => {
        const { recapsPossible: _p, recapsWhy: _y, ...rest } = await real(...a);
        return rest;
      };
    });
    expect(tick()).toBeNull();
  });

  it("says nothing about recaps while recaps are off for the site", async () => {
    live.recaps = false;
    await ready("100.recaps-plan");
    expect(tick()).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("what is sent", () => {
  it("sends writeRecap for a ticked session when possible", async () => {
    const api = await ready("100.cmp1");
    fireEvent.input(screen.getByLabelText(w.dateLabel("session-1-2026-09-14.txt")), { target: { value: "2026-09-14" } });
    fireEvent.click(tick() as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await screen.findByText(w.progressHeading);
    expect(api.calls.some((c) => c.startsWith("importStart:") && c.includes("#recap"))).toBe(true);
  });

  it("sends none when not possible", async () => {
    const api = await ready("100.recaps-plan");
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await screen.findByText(w.progressHeading);
    expect(api.calls.some((c) => c.startsWith("importStart:") && c.includes("#recap"))).toBe(false);
  });
});

describe("the setting changed between Preview and the start", () => {
  const changed = (why: ImportStarted["recapsWhy"]) => (api: ReturnType<typeof mockApi>) => {
    const real = api.importStart!.bind(api);
    api.importStart = async (...a) => ({ ...(await real(...a)), recapsPossible: false, ...(why ? { recapsWhy: why } : {}) });
  };
  const run = async (why: ImportStarted["recapsWhy"], tickIt = true) => {
    await ready("100.cmp1", changed(why));
    if (tickIt) fireEvent.click(tick() as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await screen.findByText(w.progressHeading);
  };

  it("says why on the progress view after ticks were sent", async () => {
    await run("campaign_off");
    expect(screen.getByText(w.recapsWhy.campaign_off ?? "")).toBeTruthy();
  });

  it("links the plans for a plan reason", async () => {
    await run("plan");
    expect(screen.getByRole("link", { name: w.recapsPlans }).getAttribute("href")).toBe("/pricing");
  });

  it("says that none will be written when the reason is not the DM's to change", async () => {
    await run("off");
    expect(screen.getByText(w.recapsNotWritten)).toBeTruthy();
  });

  it("says nothing when no tick was sent", async () => {
    await run("campaign_off", false);
    expect(screen.queryByText(w.recapsWhy.campaign_off ?? "")).toBeNull();
    expect(screen.queryByText(w.recapsNotWritten)).toBeNull();
  });
});

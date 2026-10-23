/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Every recap says which session it is (#2418): "<Campaign> · Session 4 · Played Tuesday 21 October
// 2026 · 2 h 35 min" on the recap page, in each list row and on the shared page. A missing part is
// left out, and an older API that sends none of it still shows the page.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import PublicRecapPage from "../src/account/PublicRecap";
import Recaps from "../src/account/Recaps";
import { playedDay, sessionLength, sessionLine } from "../src/content/recaps";

const live = vi.hoisted(() => ({ share: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_SHARE_LIVE() {
      return live.share;
    },
  };
});

afterEach(() => {
  cleanup();
  live.share = false;
});

const posted = mockRecaps().find((r) => r.status === "posted")!;
const AT = 1791000000;
const REF = "200000000000000001.cmp1.1791000000";

describe("how long a session ran", () => {
  it("says minutes under an hour, hours and minutes after, and hours alone on the hour", () => {
    expect(sessionLength(45 * 60)).toBe("45 min");
    expect(sessionLength(2 * 3600 + 35 * 60)).toBe("2 h 35 min");
    expect(sessionLength(2 * 3600)).toBe("2 h");
    expect(sessionLength(59 * 60 + 40)).toBe("1 h"); // rounded to the minute, never "60 min"
    expect(sessionLength(20)).toBe("1 min");
  });

  it("says nothing when there is no length", () => {
    for (const none of [null, undefined, 0, -5, Number.NaN]) expect(sessionLength(none)).toBe("");
  });
});

describe("the line", () => {
  const full = { campaign: "The Brynwater Crossing", sessionAt: AT, sessionNumber: 4, sessionSeconds: 9300 };

  it("has every part, in order, with the day in the reader's own language", () => {
    expect(sessionLine(full, [], "en-GB")).toBe(
      `The Brynwater Crossing · Session 4 · Played ${playedDay(AT, "en-GB")} · 2 h 35 min`,
    );
    expect(playedDay(AT, "en-GB")).toMatch(/^[A-Z][a-z]+,? \d{1,2} [A-Z][a-z]+ 2026$/);
  });

  it("puts the server's name after the campaign when asked (the list)", () => {
    expect(sessionLine(full, ["Thursday Table"], "en-GB").startsWith("The Brynwater Crossing · Thursday Table · Session 4")).toBe(true);
  });

  it("leaves out a missing part, never 'Session ?'", () => {
    expect(sessionLine({ ...full, sessionNumber: null }, [], "en-GB")).not.toMatch(/Session/);
    expect(sessionLine({ ...full, sessionSeconds: null }, [], "en-GB")).toBe(
      `The Brynwater Crossing · Session 4 · Played ${playedDay(AT, "en-GB")}`,
    );
    expect(sessionLine({ campaign: "X" }, [], "en-GB")).toBe("X");
    expect(sessionLine({}, [], "en-GB")).toBe("");
    expect(sessionLine({ ...full, sessionNumber: 0 }, [], "en-GB")).not.toMatch(/Session/);
  });
});

describe("on the pages", () => {
  it("is under the recap's title", async () => {
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${REF}`} />);
    const line = await screen.findByText(/Session 4 · Played .* · 2 h 35 min/);
    expect(line.textContent?.startsWith("The Brynwater Crossing")).toBe(true);
  });

  it("is in each row of the list", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" />);
    expect((await screen.findAllByText(/Session 4 · Played .* · 2 h 35 min/)).length).toBe(1);
    // The other recap in the list has none of it: just the campaign, the server and the day.
    expect(screen.getAllByText(/Thursday Table/).length).toBeGreaterThan(1);
  });

  it("still renders when an older API sends none of it", async () => {
    const api = mockApi("table");
    const read = api.recap!.bind(api);
    api.recap = async (r) => {
      const { sessionNumber: _n, sessionSeconds: _s, ...older } = await read(r);
      return older;
    };
    render(<Recaps api={api} path={`/account/recaps/${REF}`} />);
    await screen.findByRole("heading", { name: /The ferry at dusk/ });
    expect(screen.queryByText(/Session \d/)).toBeNull();
    expect(screen.getByText(/Thursday Table/)).toBeTruthy();
  });

  it("is on the shared page, without the campaign's name", async () => {
    live.share = true;
    const api = mockApi("table");
    await api.shareRecap!(posted.ref, "on");
    const read = api.publicRecap!.bind(api);
    api.publicRecap = async (t) => ({ ...(await read(t)), sessionNumber: 4, sessionSeconds: 2700 });
    render(<PublicRecapPage api={api} path="/r/secret1" />);
    const line = await screen.findByText(/^Session 4 · Played .* · 45 min$/);
    expect(line.textContent).not.toMatch(/Brynwater/);
  });

  it("shows nothing extra on the shared page from an older API", async () => {
    live.share = true;
    const api = mockApi("table");
    await api.shareRecap!(posted.ref, "on");
    const read = api.publicRecap!.bind(api);
    api.publicRecap = async (t) => {
      const { sessionAt: _a, sessionNumber: _n, sessionSeconds: _s, ...older } = await read(t);
      return older;
    };
    render(<PublicRecapPage api={api} path="/r/secret1" />);
    await screen.findByRole("heading", { name: posted.title });
    expect(screen.queryByText(/Session \d/)).toBeNull();
    expect(screen.queryByText(/Played /)).toBeNull();
  });
});

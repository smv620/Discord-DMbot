/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The website's "Game calendar" on a campaign's Settings (#5364 slice 3): a game master picks a calendar, sees core's own preview line
// before saving, and the save follows the bot's answer. The page works out no date: every line and problem is core's.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CalendarPanel from "../src/account/CalendarPanel";
import CampaignSettings from "../src/account/CampaignSettings";
import { mockApi } from "../src/account/mock";
import { CALENDAR_LIVE } from "../src/content/calendar-switch";
import { calendarWords as words } from "../src/content/calendar-words";
import { entries, held } from "../src/content/whats-new";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/calendar-switch", () => ({
  get CALENDAR_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
});

const REF = "100.cmp1";
const panel = (api = mockApi("table"), ref = REF) => render(<CalendarPanel api={api} campaignRef={ref} />);
const pick = (label: string) => fireEvent.click(screen.getByRole("radio", { name: label }));

describe("the game calendar on a campaign's Settings", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    const title = "Set your game's own calendar";
    expect(held.some((h) => h.title === title)).toBe(!CALENDAR_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(CALENDAR_LIVE);
  });

  it("shows the calendar now in force, in core's own words", async () => {
    const api = mockApi("table");
    panel(api);
    expect(await screen.findByText("Day 1 is Hammer 1st, 1491 DR")).toBeTruthy();
    expect((screen.getByRole("radio", { name: words.kinds.harptos }) as HTMLInputElement).checked).toBe(true);
  });

  it("Four seasons asks for the days in each season, and shows core's preview line, never one of its own", async () => {
    const api = mockApi("table");
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.seasons);
    fireEvent.input(screen.getByLabelText(words.seasonsDays), { target: { value: "90" } });
    expect(await screen.findByText(/Day 1 is Spring 1st, 1/, undefined, { timeout: 2000 })).toBeTruthy();
    const asked = api.calls.filter((c) => c.startsWith("calendarPreview:"));
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((c) => c === `calendarPreview:${REF}:custom`)).toBe(true); // (Four seasons goes as "My own")
  });

  it("a definition core refuses shows core's problems and cannot be saved", async () => {
    const api = mockApi("table");
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.custom);
    expect(await screen.findByText("A part needs a name that is not only a number.", undefined, { timeout: 2000 })).toBeTruthy();
    expect((screen.getByRole("button", { name: words.save }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.calls.some((c) => c.startsWith("saveCalendar:"))).toBe(false);
  });

  it("saves, follows the bot's answer, says saved, and reads the calendar again", async () => {
    const api = mockApi("table");
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.seasons);
    await screen.findByText(/Day 1 is Spring/, undefined, { timeout: 2000 });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(await screen.findByText(words.saved, undefined, { timeout: 6000 })).toBeTruthy();
    expect(api.calls).toContain(`saveCalendar:${REF}:custom`);
    expect(api.calls.filter((c) => c === `calendar:${REF}`).length).toBe(2);
  }, 10_000);

  it("a save the bot refuses says core's words", async () => {
    const ref = "100.cmp1.cal-refused";
    panel(mockApi("table"), ref);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.seasons);
    await screen.findByText(/Day 1 is Spring/, undefined, { timeout: 2000 });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(await screen.findByText("A part needs a name that is not only a number.", undefined, { timeout: 6000 })).toBeTruthy();
    expect(screen.queryByText(words.saved)).toBeNull();
  }, 10_000);

  it("leaving a calendar of the DM's own for Days says it will be removed and asks once", async () => {
    const api = mockApi("table");
    api.calendar = async () => ({
      kind: "custom",
      def: { name: "Mine", parts: [{ name: "Spring", days: 90, kind: "season" }], leap: null, weekdays: [], yearLabel: "AR" },
      dayOne: { year: 1201, part: "Spring", day: 2, weekday: null },
      line: "Day 1 is Spring 2nd, 1201 AR",
      hasClock: true,
    });
    panel(api);
    await screen.findByText("Day 1 is Spring 2nd, 1201 AR");
    pick(words.kinds.days);
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(screen.getByText(words.removeWarning)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("saveCalendar:"))).toBe(false); // (nothing sent yet)
    fireEvent.click(screen.getByRole("button", { name: words.removeKeep }));
    expect(screen.queryByText(words.removeWarning)).toBeNull();
    expect((screen.getByRole("radio", { name: words.kinds.custom }) as HTMLInputElement).checked).toBe(true);
    pick(words.kinds.days);
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    fireEvent.click(screen.getByRole("button", { name: words.removeSwitch }));
    await waitFor(() => expect(api.calls).toContain(`saveCalendar:${REF}:days`));
  });

  it("with no game clock it says so and cannot be saved", async () => {
    panel(mockApi("table"), "100.cmp1.cal-noclock");
    await screen.findByText(words.noClock);
    expect((screen.getByRole("button", { name: words.save }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says once that dates already counted stay on their day", async () => {
    panel();
    await screen.findByText(/Day 1 is Hammer/);
    expect(screen.getAllByText(words.stays)).toHaveLength(1);
  });

  it("a player gets nothing", async () => {
    const { container } = panel(mockApi("table"), "100.cmp1.player");
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("is on the campaign's Settings only while the switch is on, and only for a game master", async () => {
    const api = mockApi("table");
    const page = await api.campaignPage!(REF);
    const { unmount } = render(<CampaignSettings api={api} campaignRef={REF} page={page} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
    expect(api.calls.some((c) => c.startsWith("calendar:"))).toBe(false); // (nothing asked while it is off)
    unmount();
    live.on = true;
    render(<CampaignSettings api={api} campaignRef={REF} page={page} />);
    expect(await screen.findByRole("heading", { name: words.heading })).toBeTruthy();
    cleanup();
    const { settings: _gone, ...player } = page;
    render(<CampaignSettings api={api} campaignRef={REF} page={player} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
  });
});

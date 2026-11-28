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

  it("several weekdays can be typed, commas included", async () => {
    const api = mockApi("table");
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.custom);
    const box = screen.getByLabelText(new RegExp(`^${words.weekdays}`)) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "Mon," } });
    expect(box.value).toBe("Mon,"); // (the comma stays while the next one is typed)
    fireEvent.input(box, { target: { value: "Mon, Tue" } });
    await waitFor(() => expect(screen.getByRole("combobox", { name: words.dayOneWeekday })).toBeTruthy());
    expect(Array.from((screen.getByRole("combobox", { name: words.dayOneWeekday }) as HTMLSelectElement).options).map((o) => o.value)).toEqual(["Mon", "Tue"]);
  });

  it("what is sent is what is shown: the first weekday is really chosen, and a one-day festival has no day number", async () => {
    const api = mockApi("table");
    api.calendar = async () => ({
      kind: "custom",
      def: { name: "Mine", parts: [{ name: "Spring", days: 30, kind: "month" }, { name: "Midwinter", days: 1, kind: "festival" }], leap: null, weekdays: ["Mon", "Tue"], yearLabel: "AR" },
      dayOne: { year: 1201, part: "Spring", day: 2, weekday: null },
      line: "Day 1 is Spring 2nd, 1201 AR",
      hasClock: true,
    });
    const asked: unknown[] = [];
    const real = api.calendarPreview!.bind(api);
    api.calendarPreview = async (ref, body) => (asked.push(body), real(ref, body));
    panel(api);
    await screen.findByText("Day 1 is Spring 2nd, 1201 AR");
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect((asked[asked.length - 1] as { dayOne: { weekday: string } }).dayOne.weekday).toBe("Mon"); // (not null: what the select shows)
    fireEvent.change(screen.getByRole("combobox", { name: words.dayOnePart }), { target: { value: "Midwinter" } });
    await waitFor(() => expect((asked[asked.length - 1] as { dayOne: { part: string; day: number | null } }).dayOne).toMatchObject({ part: "Midwinter", day: null }));
  });

  it("saving over a calendar of the DM's own says it replaces it, with Save and Cancel (#5364 ruling)", async () => {
    const api = mockApi("table");
    api.calendar = async () => ({
      kind: "custom",
      def: { name: "Mine", parts: [{ name: "Spring", days: 30, kind: "month" }], leap: null, weekdays: [], yearLabel: "" },
      dayOne: { year: 1, part: "Spring", day: 1, weekday: null },
      line: "Day 1 is Spring 1st, year 1 AR",
      hasClock: true,
    });
    panel(api);
    await screen.findByText("Day 1 is Spring 1st, year 1 AR");
    pick(words.kinds.seasons);
    await screen.findByText(/Day 1 is Spring 1st, 1$/, undefined, { timeout: 2000 }); // (core's preview for Four seasons, not the line now in force)
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(screen.getByText(words.replaceWarning)).toBeTruthy();
    expect(screen.queryByText(words.removeWarning)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("saveCalendar:"))).toBe(false); // (nothing sent until Save is pressed again)
    fireEvent.click(screen.getByRole("button", { name: words.replaceCancel }));
    expect(screen.queryByText(words.replaceWarning)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("saveCalendar:"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    fireEvent.click(screen.getByRole("button", { name: words.replaceSave }));
    await waitFor(() => expect(api.calls).toContain(`saveCalendar:${REF}:custom`));
  });

  it("core can refuse the save before queueing it, or say there is no clock or it is busy, in its own words", async () => {
    for (const answer of [{ problems: ["Two parts have the same name."] }, { error: "no_clock" as const, words: "Set the game clock first." }, { error: "busy" as const, words: "Another change is being made." }]) {
      const api = mockApi("table");
      api.saveCalendar = async () => answer;
      const view = panel(api);
      await screen.findByText(/Day 1 is Hammer/);
      pick(words.kinds.seasons);
      await screen.findByText(/Day 1 is Spring/, undefined, { timeout: 2000 });
      fireEvent.click(screen.getByRole("button", { name: words.save }));
      const said = "problems" in answer ? answer.problems[0] : answer.words;
      expect(await screen.findByText(said as string)).toBeTruthy();
      expect(screen.queryByText(words.saved)).toBeNull();
      view.unmount();
    }
  });

  it("the voice's year phrase is asked for only with a year name, and goes with the definition", async () => {
    const api = mockApi("table");
    const asked: { def?: { yearSay?: string } }[] = [];
    const real = api.calendarPreview!.bind(api);
    api.calendarPreview = async (ref, body) => (asked.push(body), real(ref, body));
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.custom);
    expect(screen.queryByLabelText(new RegExp(`^${words.yearSay}`))).toBeNull();
    fireEvent.input(screen.getByLabelText(words.yearLabel), { target: { value: "AR" } });
    fireEvent.input(await screen.findByLabelText(new RegExp(`^${words.yearSay}`)), { target: { value: "After Reckoning" } });
    await waitFor(() => expect(asked[asked.length - 1]?.def?.yearSay).toBe("After Reckoning"));
  });

  it("a double press sends one save", async () => {
    const api = mockApi("table");
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.seasons);
    await screen.findByText(/Day 1 is Spring/, undefined, { timeout: 2000 });
    const save = screen.getByRole("button", { name: words.save });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("saveCalendar:")).length).toBe(1));
  });

  it("a preview that cannot be made says so and Save stays off", async () => {
    const api = mockApi("table");
    api.calendarPreview = async () => {
      throw new Error("down");
    };
    panel(api);
    await screen.findByText(/Day 1 is Hammer/);
    pick(words.kinds.seasons);
    expect(await screen.findByText(words.previewFailed, undefined, { timeout: 2000 })).toBeTruthy();
    expect((screen.getByRole("button", { name: words.save }) as HTMLButtonElement).disabled).toBe(true);
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

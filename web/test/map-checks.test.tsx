/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The website's "Map checks" switch on a campaign's Settings (#4687): a game master turns it on or off, the page follows
// the bot's answer, and a player never sees it.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import MapChecksPanel from "../src/account/MapChecksPanel";
import { MAP_CHECKS_LIVE } from "../src/content/map-checks-switch";
import { mapChecksWords as words } from "../src/content/map-checks-words";
import { entries, held } from "../src/content/whats-new";

// The switch is off unless a test turns it on for the settings page (#4687), as share-story.test.tsx mocks its switch.
const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/map-checks-switch", () => ({
  get MAP_CHECKS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
});

describe("Map checks on the campaign's Settings", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    const title = "Turn the map notes off or on from the website";
    expect(held.some((h) => h.title === title)).toBe(!MAP_CHECKS_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(MAP_CHECKS_LIVE);
  });

  it("shows on or off from the page's read", () => {
    const { unmount } = render(<MapChecksPanel api={mockApi("table")} campaignRef="100.cmp1" on={true} />);
    expect(screen.getByRole("button", { name: words.on }).getAttribute("aria-pressed")).toBe("true");
    unmount();
    render(<MapChecksPanel api={mockApi("table")} campaignRef="100.cmp1" on={false} />);
    expect(screen.getByRole("button", { name: words.off }).getAttribute("aria-pressed")).toBe("false");
  });

  it("pressing it sends map_checks off, then on, once each, and asks nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<MapChecksPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:map_checks:off"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    await waitFor(() => expect(screen.getByRole("button", { name: words.off })).toBeTruthy());
    expect(screen.getByText(words.saved)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.off }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:map_checks:on"));
    expect(api.calls.filter((c) => c.includes(":map_checks:")).length).toBe(2);
  });

  it("a second press while one waits sends nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<MapChecksPanel api={api} campaignRef="100.cmp1" on={true} />);
    const button = screen.getByRole("button", { name: words.on });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(api.calls.filter((c) => c.includes(":map_checks:")).length).toBe(1);
  });

  it("a refused change says so and changes nothing on the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.setCampaignSetting = async () => {
      throw new ApiError("not-allowed");
    };
    render(<MapChecksPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    expect(await screen.findByText(words.problems["not-allowed"] as string)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.on }).getAttribute("aria-pressed")).toBe("true");
  });

  it("is shown to a game master while the switch is on", async () => {
    live.on = true;
    const api = mockApi("table");
    const page = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    expect(await screen.findByRole("heading", { name: words.heading })).toBeTruthy();
  });

  it("is not shown while the switch is off", async () => {
    const api = mockApi("table");
    const page = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
  });

  it("a player's page has no switch, even with the switch on", async () => {
    live.on = true;
    const api = mockApi("table");
    const { settings: _gone, ...page } = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
  });
});

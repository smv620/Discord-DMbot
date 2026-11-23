/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The website's "Rules cards" switch on a campaign's Settings (#5155): a game master turns it on or off, the page follows
// the bot's answer, and a player never sees it.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RulesCardsPanel from "../src/account/RulesCardsPanel";
import { RULES_CARDS_LIVE } from "../src/content/rules-cards-switch";
import { rulesCardsWords as words } from "../src/content/rules-cards-words";
import { entries, held } from "../src/content/whats-new";

// The switch is off unless a test turns it on for the settings page (#5155), as map-checks.test.tsx mocks its switch.
const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/rules-cards-switch", () => ({
  get RULES_CARDS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
});

describe("Rules cards on the campaign's Settings", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    const title = "Rules cards switch on the website";
    expect(held.some((h) => h.title === title)).toBe(!RULES_CARDS_LIVE);
    expect(entries.some((e) => e.title === title)).toBe(RULES_CARDS_LIVE);
  });

  it("shows on or off from the page's read", () => {
    const { unmount } = render(<RulesCardsPanel api={mockApi("table")} campaignRef="100.cmp1" on={true} />);
    expect(screen.getByRole("button", { name: words.on }).getAttribute("aria-pressed")).toBe("true");
    unmount();
    render(<RulesCardsPanel api={mockApi("table")} campaignRef="100.cmp1" on={false} />);
    expect(screen.getByRole("button", { name: words.off }).getAttribute("aria-pressed")).toBe("false");
  });

  it("pressing it sends rules_cards off, then on, once each, and asks nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RulesCardsPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:rules_cards:off"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    await waitFor(() => expect(screen.getByRole("button", { name: words.off })).toBeTruthy());
    expect(screen.getByText(words.saved)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.off }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:rules_cards:on"));
    expect(api.calls.filter((c) => c.includes(":rules_cards:")).length).toBe(2);
  });

  it("a second press while one waits sends nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RulesCardsPanel api={api} campaignRef="100.cmp1" on={true} />);
    const button = screen.getByRole("button", { name: words.on });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(api.calls.filter((c) => c.includes(":rules_cards:")).length).toBe(1);
  });

  it("a refused change says so and changes nothing on the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.setCampaignSetting = async () => {
      throw new ApiError("not-allowed");
    };
    render(<RulesCardsPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    expect(await screen.findByText(words.problems["not-allowed"] as string)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.on }).getAttribute("aria-pressed")).toBe("true");
  });

  it("a change the bot refuses says nothing was changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.setCampaignSetting = async () => "rc1";
    api.campaignSettingAnswer = async () => ({ state: "failed", error: "refused" }) as Awaited<ReturnType<NonNullable<typeof api.campaignSettingAnswer>>>;
    render(<RulesCardsPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(await screen.findByText(words.failed)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.on }).getAttribute("aria-pressed")).toBe("true");
  });

  it("says the slow line after a minute with no answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async (_ref, _id) => ({ state: "working" } as Awaited<ReturnType<NonNullable<typeof api.campaignSettingAnswer>>>);
    render(<RulesCardsPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.on }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });
    expect(await screen.findByText(words.quiet)).toBeTruthy();
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

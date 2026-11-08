/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Share this campaign's story to improve DMbot" (#3741): a game master turns it on or off, and follows the bot's answer.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import ShareStoryPanel from "../src/account/ShareStoryPanel";
import { SHARE_STORY_LIVE } from "../src/content/share-story-switch";
import { shareStoryWords as words } from "../src/content/share-story-words";
import { entries, held } from "../src/content/whats-new";

// The switch is off unless a test turns it on for the settings page (#3741), as web-unnamed.test.tsx mocks its switches.
const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/share-story-switch", () => ({
  get SHARE_STORY_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = false;
});

describe("Share your story to improve DMbot", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "Share your story to improve DMbot")).toBe(!SHARE_STORY_LIVE);
    expect(entries.some((e) => e.title === "Share your story to improve DMbot")).toBe(SHARE_STORY_LIVE);
  });

  it("shows the state and offers the other choice", () => {
    render(<ShareStoryPanel api={mockApi("table")} campaignRef="100.cmp1" on={false} />);
    expect(screen.getByText(words.offState)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.turnOn })).toBeTruthy();
  });

  it("asks before turning it on, and sends share_story on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<ShareStoryPanel api={api} campaignRef="100.cmp1" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    expect(screen.getByText(words.askOn)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:share_story:on"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    // The panel now offers the other choice: the state has flipped.
    await waitFor(() => expect(screen.getByRole("button", { name: words.turnOff })).toBeTruthy());
  });

  it("turning it off asks nothing, and sends share_story off", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<ShareStoryPanel api={api} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOff }));
    expect(screen.queryByText(words.askOn)).toBeNull();
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:share_story:off"));
  });

  it("Cancel changes nothing", () => {
    const api = mockApi("table");
    render(<ShareStoryPanel api={api} campaignRef="100.cmp1" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.cancel }));
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting:"))).toBe(false);
    expect(screen.getByText(words.offState)).toBeTruthy();
  });

  it("says it is slow to answer when nothing comes back, and gives up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => ({ setting: "share_story", value: "on", note: "", error: "", state: "waiting" } as never);
    render(<ShareStoryPanel api={api} campaignRef="100.cmp1" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000);
    });
    expect(await screen.findByText(words.quiet)).toBeTruthy();
    expect(screen.getByText(words.offState)).toBeTruthy();
  });

  it("a look that fails after the choice was sent says it is slow, not that it failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => {
      throw new ApiError("not-found");
    };
    render(<ShareStoryPanel api={api} campaignRef="100.cmp1" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(await screen.findByText(words.quiet)).toBeTruthy();
    expect(screen.queryByText(words.failed)).toBeNull();
  });

  it("turning it off shows the off line, with what was removed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShareStoryPanel api={mockApi("table")} campaignRef="100.cmp1" on={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOff }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(await screen.findByText(words.doneOff)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.turnOn })).toBeTruthy();
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

  it("a player's page never shows it, even with the switch on", async () => {
    live.on = true;
    const api = mockApi("table");
    const { settings: _gone, ...page } = await api.campaignPage!("100.cmp1");
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={page} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
  });

  it("a failure says nothing was changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShareStoryPanel api={mockApi("table")} campaignRef="100.cmp1.ss-failed" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(await screen.findByText(words.failed)).toBeTruthy();
    expect(screen.getByText(words.offState)).toBeTruthy();
  });

  it("a refused player is told that only a game master can change it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShareStoryPanel api={mockApi("table")} campaignRef="100.cmp1.cp-player" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    expect(await screen.findByText(words.problems["not-allowed"] ?? "")).toBeTruthy();
  });
});

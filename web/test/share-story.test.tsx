/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Share this campaign's story to improve DMbot" (#3741): a game master turns it on or off, and follows the bot's answer.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import ShareStoryPanel from "../src/account/ShareStoryPanel";
import { SHARE_STORY_LIVE } from "../src/content/share-story-switch";
import { shareStoryWords as words } from "../src/content/share-story-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
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
    // The done line and the state line both say "Sharing is on.", so the page shows it twice.
    expect((await screen.findAllByText(words.onState)).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: words.turnOff })).toBeTruthy();
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

  it("a refused player is told to ask a game master", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShareStoryPanel api={mockApi("table")} campaignRef="100.cmp1.cp-player" on={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.turnOn }));
    fireEvent.click(screen.getByRole("button", { name: words.yesOn }));
    expect(await screen.findByText(words.problems["not-allowed"])).toBeTruthy();
  });
});

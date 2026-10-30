/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Pause / Unpause on a campaign's page (#2985): the owner asks, confirms, and follows the bot's answer.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import PausePanel from "../src/account/PausePanel";
import { PAUSE_LIVE } from "../src/content/pause-switch";
import { pausePanel as words } from "../src/content/pause-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Pause on the website", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "Pause a campaign on the website")).toBe(!PAUSE_LIVE);
    expect(entries.some((e) => e.title === "Pause a campaign on the website")).toBe(PAUSE_LIVE);
  });

  it("asks first, and sends nothing on Never mind", () => {
    const api = mockApi("table");
    render(<PausePanel api={api} campaignRef="100.cmp1" paused={false} />);
    expect(screen.getByText(words.runningNow)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    expect(screen.getByText(words.pauseAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.keep }));
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting:"))).toBe(false);
  });

  it("pauses, follows the answer and then offers Unpause", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<PausePanel api={api} campaignRef="100.cmp1" paused={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesPause }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:pause::on"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(words.pausedNow)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.unpauseButton })).toBeTruthy();
  });

  it("says the bot's own words when there is no room to unpause, and stays paused", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<PausePanel api={api} campaignRef="100.cmp1.pa-noroom" paused={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.unpauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesUnpause }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(/no free place on your plan/)).toBeTruthy();
    expect(screen.getByText(words.pausedNow)).toBeTruthy();
  });

  it("says why when the server refuses", async () => {
    const api = mockApi("table");
    api.setCampaignSetting = async () => {
      const { ApiError } = await import("../src/account/api");
      throw new ApiError("not-owner");
    };
    render(<PausePanel api={api} campaignRef="100.cmp1" paused={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesPause }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.problems["not-owner"]);
  });
});

describe("the wait and the focus (reviewer and ux-critic, #2985)", () => {
  it("says so when the answer never comes, and gives the button back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<PausePanel api={api} campaignRef="100.cmp1.cs-offline" paused={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesPause }));
    for (let i = 0; i < 50; i++) await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(await screen.findByText(words.timedOut)).toBeTruthy();
    expect((screen.getByRole("button", { name: words.pauseButton }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not say it failed when only the look at the answer failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => {
      throw new Error("network");
    };
    render(<PausePanel api={api} campaignRef="100.cmp1" paused={false} />);
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesPause }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(words.timedOut)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("tells the page when it is done, and puts the focus on the question", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const seen: boolean[] = [];
    render(<PausePanel api={mockApi("table")} campaignRef="100.cmp1" paused={false} onChanged={(p) => seen.push(p)} />);
    fireEvent.click(screen.getByRole("button", { name: words.pauseButton }));
    expect(document.activeElement?.textContent).toBe(words.yesPause);
    fireEvent.click(screen.getByRole("button", { name: words.yesPause }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    await waitFor(() => expect(seen).toEqual([true]));
  });

  it("names the state after a refusal, with the next step for a full plan", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<PausePanel api={mockApi("table")} campaignRef="100.cmp1.pa-noroom" paused={true} />);
    fireEvent.click(screen.getByRole("button", { name: words.unpauseButton }));
    fireEvent.click(screen.getByRole("button", { name: words.yesUnpause }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(new RegExp(words.stillPaused))).toBeTruthy();
    expect(document.body.textContent).toContain(words.noRoomNext);
  });
});

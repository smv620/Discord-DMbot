/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Shared story choice on the website (#2985): off, watch or guide; turning it on asks for the right-to-use
// confirmation first and sends it; the bot's own words are shown as plain text.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import StoryModePanel from "../src/account/StoryModePanel";
import { STORY_MODE_LIVE } from "../src/content/story-mode-switch";
import { storyModePanel as words } from "../src/content/story-mode-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const TITLE = "Choose how DMbot follows your adventure";

describe("The Shared story choice on the website", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === TITLE)).toBe(!STORY_MODE_LIVE);
    expect(entries.some((e) => e.title === TITLE)).toBe(STORY_MODE_LIVE);
  });

  it("says where it stands and offers the other two choices", () => {
    render(<StoryModePanel api={mockApi("table")} campaignRef="100.cmp1" mode="watch" />);
    expect(screen.getByText(words.now("watch"))).toBeTruthy();
    expect(screen.getByRole("button", { name: words.choose("off") })).toBeTruthy();
    expect(screen.getByRole("button", { name: words.choose("guide") })).toBeTruthy();
    expect(screen.queryByRole("button", { name: words.choose("watch") })).toBeNull();
  });

  it("asks for the right-to-use box before turning it on, and sends nothing on Keep", () => {
    const api = mockApi("table");
    render(<StoryModePanel api={api} campaignRef="100.cmp1" mode="off" />);
    fireEvent.click(screen.getByRole("button", { name: words.choose("watch") }));
    expect(screen.getByText(words.warning)).toBeTruthy();
    expect((screen.getByRole("button", { name: words.confirm }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: words.keep }));
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting:"))).toBe(false);
    expect(screen.getByRole("button", { name: words.choose("watch") })).toBeTruthy();
  });

  it("sends the confirmation with a turn-on and shows the new state when done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    const seen: string[] = [];
    render(<StoryModePanel api={api} campaignRef="100.cmp1" mode="off" onChanged={(m) => seen.push(m)} />);
    fireEvent.click(screen.getByRole("button", { name: words.choose("guide") }));
    fireEvent.click(screen.getByRole("checkbox", { name: words.rightLabel }));
    fireEvent.click(screen.getByRole("button", { name: words.confirm }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:story_mode:guide:confirm"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(words.now("guide"))).toBeTruthy();
    expect(seen).toEqual(["guide"]);
  });

  it("turns it off at once with no question and no confirmation", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<StoryModePanel api={api} campaignRef="100.cmp1" mode="guide" />);
    fireEvent.click(screen.getByRole("button", { name: words.choose("off") }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:story_mode:off"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(words.now("off"))).toBeTruthy();
  });

  it("shows the bot's words as plain text with the state it stayed in", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<StoryModePanel api={mockApi("table")} campaignRef="100.cmp1.sm-refused" mode="off" />);
    fireEvent.click(screen.getByRole("button", { name: words.choose("watch") }));
    fireEvent.click(screen.getByRole("checkbox", { name: words.rightLabel }));
    fireEvent.click(screen.getByRole("button", { name: words.confirm }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(await screen.findByText(/Shared story needs a confirmation\./)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\*|`/);
    expect(document.body.textContent).toContain(words.stillThat("off"));
  });

  it("says so when the server refuses", async () => {
    const api = mockApi("table");
    api.setCampaignSetting = async () => {
      const { ApiError } = await import("../src/account/api");
      throw new ApiError("needs-confirm");
    };
    render(<StoryModePanel api={api} campaignRef="100.cmp1" mode="off" />);
    fireEvent.click(screen.getByRole("button", { name: words.choose("watch") }));
    fireEvent.click(screen.getByRole("checkbox", { name: words.rightLabel }));
    fireEvent.click(screen.getByRole("button", { name: words.confirm }));
    expect(await screen.findByText(words.problems["needs-confirm"]!)).toBeTruthy();
  });
});

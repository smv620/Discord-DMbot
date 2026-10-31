/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Main rules and "If the main rules don't cover something" on the website (#2985): the same two choices as Discord's
// picker; the second can never be the first; one choice is sent as "<main>/<fallback>"; the bot's own words are plain text.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RulesetsPanel from "../src/account/RulesetsPanel";
import { RULESETS_LIVE } from "../src/content/rulesets-switch";
import { FALLBACK_NONE, MAIN_CHOICES, rulesetsPanel as words } from "../src/content/rulesets-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const TITLE = "Choose your main rules on the website";

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
}

const radio = (name: string) => screen.getByLabelText(name) as HTMLInputElement;

describe("Main rules on the website", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === TITLE)).toBe(!RULESETS_LIVE);
    expect(entries.some((e) => e.title === TITLE)).toBe(RULESETS_LIVE);
  });

  it("offers exactly the rules the bot offers", () => {
    expect([...MAIN_CHOICES]).toEqual(["2024", "2014"]);
    expect(Object.keys(words.main).sort()).toEqual([...MAIN_CHOICES].sort()); // (number-like keys come out in order)
    expect(Object.keys(words.fallback).sort()).toEqual([...MAIN_CHOICES, FALLBACK_NONE].sort());
  });

  it("shows where it stands and starts on those choices", () => {
    render(<RulesetsPanel api={mockApi("table")} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    expect(screen.getByText(words.now("2024", "2014"))).toBeTruthy();
    expect(radio(words.main["2024"]).checked).toBe(true);
    expect(radio(words.fallback["2014"]).checked).toBe(true);
    expect((screen.getByRole("button", { name: words.save }) as HTMLButtonElement).disabled).toBe(true); // (nothing to save yet)
  });

  it("never offers the main rules as the second set", () => {
    render(<RulesetsPanel api={mockApi("table")} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    expect(screen.queryByLabelText(words.fallback["2024"])).toBeNull();
    fireEvent.click(radio(words.main["2014"]));
    expect(screen.queryByLabelText(words.fallback["2014"])).toBeNull();
    expect(screen.getByLabelText(words.fallback["2024"])).toBeTruthy();
    expect(screen.getByLabelText(words.fallback.none)).toBeTruthy();
  });

  it("changing the main rules to the one that was the second set moves the second set to skip it", () => {
    render(<RulesetsPanel api={mockApi("table")} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.main["2014"]));
    expect(radio(words.fallback.none).checked).toBe(true);
  });

  it("sends one choice as main/fallback and shows the new state when done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    const seen: string[] = [];
    render(<RulesetsPanel api={api} campaignRef="100.cmp1" main="2024" fallback="2014" onChanged={(m, f) => seen.push(`${m}/${f}`)} />);
    fireEvent.click(radio(words.main["2014"]));
    fireEvent.click(radio(words.fallback["2024"]));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:rulesets:2014/2024"));
    await settle();
    expect(await screen.findByText(words.now("2014", "2024"))).toBeTruthy();
    expect(seen).toEqual(["2014/2024"]);
    expect(document.body.textContent).toContain(words.done);
  });

  it("can skip the second set", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RulesetsPanel api={api} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.fallback.none));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:rulesets:2024/none"));
  });

  it("shows the bot's words as plain text and puts the choice back to what is true", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<RulesetsPanel api={mockApi("table")} campaignRef="100.cmp1.rs-refused" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.fallback.none));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await settle();
    expect(await screen.findByText(/Your plan can't change the rules\./)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\*|`/);
    expect(radio(words.fallback["2014"]).checked).toBe(true);
    expect(screen.getByText(words.now("2024", "2014"))).toBeTruthy();
    expect(document.body.textContent).toContain(words.failedNext); // (a next step even when the bot gave words)
    expect(document.body.textContent).toContain(words.still("2024", "2014"));
  });

  it("a failure with no words from the bot still says what to do next", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => ({ state: "failed", error: "failed", setting: "rulesets", value: "", note: "" });
    render(<RulesetsPanel api={api} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.fallback.none));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await settle();
    expect(await screen.findByText(new RegExp(words.failedNext))).toBeTruthy();
  });

  it("says so when the server refuses the pair", async () => {
    const api = mockApi("table");
    api.setCampaignSetting = async () => { throw new ApiError("bad-value"); };
    render(<RulesetsPanel api={api} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.fallback.none));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(await screen.findByText(words.problems["bad-value"]!)).toBeTruthy();
  });

  it("does not call a lost answer a failure, and says to check back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => ({ state: "waiting", error: "", setting: "rulesets", value: "", note: "" });
    render(<RulesetsPanel api={api} campaignRef="100.cmp1" main="2024" fallback="2014" />);
    fireEvent.click(radio(words.fallback.none));
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await act(async () => { await vi.advanceTimersByTimeAsync(70_000); });
    expect(await screen.findByText(words.timedOut)).toBeTruthy();
    expect(screen.getByText(words.now("2024", "2014"))).toBeTruthy();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The D&D Beyond invite on the website (#2985): a game master pastes, changes or removes the campaign's invite link;
// a wrong paste is caught at once with Discord's words; the link is saved in its standard form; the bot's own words are
// shown as plain text.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import InvitePanel from "../src/account/InvitePanel";
import { mockApi } from "../src/account/mock";
import { INVITE_LIVE } from "../src/content/invite-switch";
import { INVITE_PATTERN, invitePanel as words } from "../src/content/invite-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const TITLE = "Campaign invite from dndbeyond.com";
const LINK = "https://www.dndbeyond.com/campaigns/join/AbC123xyz";

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
}

describe("The D&D Beyond invite on the website", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === TITLE)).toBe(!INVITE_LIVE);
    expect(entries.some((e) => e.title === TITLE)).toBe(INVITE_LIVE);
  });

  it("accepts exactly the shape Discord accepts", () => {
    for (const ok of [LINK, "https://dndbeyond.com/campaigns/join/AbC123xyz", `${LINK}/`]) expect(INVITE_PATTERN.test(ok), ok).toBe(true);
    for (const bad of ["http://www.dndbeyond.com/campaigns/join/AbC123xyz", "https://www.dndbeyond.com.evil.example/campaigns/join/AbC123xyz", "https://www.dndbeyond.com/campaigns/join/ab", `look at ${LINK}`, `${LINK}?x=1`, ""]) {
      expect(INVITE_PATTERN.test(bad), bad).toBe(false);
    }
  });

  it("says there is no link yet and offers to add one", () => {
    render(<InvitePanel api={mockApi("table")} campaignRef="100.cmp1" invite="" />);
    expect(screen.getByText(words.none)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.add })).toBeTruthy();
    expect(screen.queryByRole("button", { name: words.remove })).toBeNull();
  });

  it("shows the DM their link and offers to change or remove it", () => {
    render(<InvitePanel api={mockApi("table")} campaignRef="100.cmp1" invite={LINK} />);
    expect(screen.getByText(words.now(LINK))).toBeTruthy();
    expect(screen.getByRole("button", { name: words.change })).toBeTruthy();
    expect(screen.getByRole("button", { name: words.remove })).toBeTruthy();
  });

  it("catches a wrong paste at once with Discord's words and sends nothing", () => {
    const api = mockApi("table");
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: "https://example.com/campaigns/join/AbC123xyz" } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(screen.getByText(words.notAnInvite)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting"))).toBe(false);
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(screen.getByText(words.nothingYet)).toBeTruthy();
  });

  it("saves the link in its standard form and shows it when done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    const seen: string[] = [];
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" onChanged={(v) => seen.push(v)} />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: " https://dndbeyond.com/campaigns/join/AbC123xyz/ " } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await waitFor(() => expect(api.calls).toContain(`setCampaignSetting:100.cmp1:dndbeyond_invite:${LINK}`));
    await settle();
    expect(await screen.findByText(words.now(LINK))).toBeTruthy();
    expect(seen).toEqual([LINK]);
    expect(document.body.textContent).toContain(words.done.set);
  });

  it("removes the link with an empty value", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite={LINK} />);
    fireEvent.click(screen.getByRole("button", { name: words.remove }));
    await waitFor(() => expect(api.calls).toContain("setCampaignSetting:100.cmp1:dndbeyond_invite:"));
    await settle();
    expect(await screen.findByText(words.none)).toBeTruthy();
    expect(document.body.textContent).toContain(words.done.remove);
  });

  it("shows the bot's words as plain text and says the link stayed as it was", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<InvitePanel api={mockApi("table")} campaignRef="100.cmp1.di-refused" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: LINK } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await settle();
    expect(await screen.findByText(/Your plan doesn't allow a join button\./)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\*|`/);
    expect(document.body.textContent).toContain(words.stillNow(""));
  });

  it("says so when the server refuses the link", async () => {
    const api = mockApi("table");
    api.setCampaignSetting = async () => { throw new ApiError("bad-value"); };
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: LINK } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(await screen.findByText(words.problems["bad-value"]!)).toBeTruthy();
  });

  it("does not call a lost answer a failure, and says to check back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => ({ state: "waiting", error: "", setting: "dndbeyond_invite", value: "", note: "" });
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite={LINK} />);
    fireEvent.click(screen.getByRole("button", { name: words.remove }));
    await act(async () => { await vi.advanceTimersByTimeAsync(70_000); });
    expect(await screen.findByText(words.timedOut)).toBeTruthy();
    expect(screen.getByText(words.now(LINK))).toBeTruthy();
  });

  it("shows its own message for a paste the browser would call not a URL", () => {
    const api = mockApi("table");
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    const form = screen.getByLabelText(words.label).closest("form")!;
    expect(form.noValidate).toBe(true); // (else the browser's own bubble hides ours)
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: "www.dndbeyond.com/campaigns/join/abcd" } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    expect(screen.getByText(words.notAnInvite)).toBeTruthy();
    expect(screen.getByLabelText(words.label).getAttribute("aria-describedby")).toBe("invite-help");
  });

  it("a failure with no words from the bot still says what to do next", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.campaignSettingAnswer = async () => ({ state: "failed", error: "failed", setting: "dndbeyond_invite", value: "", note: "" });
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.input(screen.getByLabelText(words.label), { target: { value: LINK } });
    fireEvent.click(screen.getByRole("button", { name: words.save }));
    await settle();
    expect(await screen.findByText(new RegExp(words.failedNext))).toBeTruthy();
  });

  it("Cancel closes the form and sends nothing", () => {
    const api = mockApi("table");
    render(<InvitePanel api={api} campaignRef="100.cmp1" invite="" />);
    fireEvent.click(screen.getByRole("button", { name: words.add }));
    fireEvent.click(screen.getByRole("button", { name: words.cancel }));
    expect(screen.getByRole("button", { name: words.add })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting"))).toBe(false);
  });
});

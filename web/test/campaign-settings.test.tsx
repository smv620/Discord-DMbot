/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Settings controls on a campaign's page (#2484): a DM changes each choice through the queue, a
// shorter keep-time is asked about first, a bot that is away is said so, and a player is shown nothing.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignPageView from "../src/account/CampaignPage";
import { mockApi } from "../src/account/mock";
import { campaignPage as w } from "../src/content/campaign-page";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const open = async (ref: string) => {
  const api = mockApi("table");
  render(
    <div class="account">
      <h1>Campaign</h1>
      <CampaignPageView api={api} path={`/account/campaigns/${ref}`} />
    </div>,
  );
  const section = (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
  return { api, section };
};
const group = (section: HTMLElement, name: keyof typeof w.controls) => within(section).getByRole("group", { name: w.controls[name] });
const slow = { timeout: 5000 };

describe("changing a choice", () => {
  it("sends each of the three and shows Saved, with the new choice pressed", async () => {
    const { section } = await open("100.cmp1");
    for (const [name, text] of [
      ["visibility", "🔒 Only the DM"],
      ["level", "🔇 Quiet"],
      ["retention", "1 year"],
    ] as const) {
      fireEvent.click(within(group(section, name)).getByRole("button", { name: text }));
      await waitFor(() => expect(within(group(section, name)).getByRole("button", { name: text }).getAttribute("aria-pressed")).toBe("true"), slow);
      expect(within(section).getAllByText(/Saved\./).length).toBeGreaterThan(0);
    }
  });

  it("waits with Saving… and locks the buttons meanwhile", async () => {
    const { section } = await open("100.cmp1");
    fireEvent.click(within(group(section, "level")).getByRole("button", { name: "🔇 Quiet" }));
    expect(await within(section).findByText(w.saving)).toBeTruthy();
    expect((within(group(section, "level")).getByRole("button", { name: "🔔 Normal" }) as HTMLButtonElement).disabled).toBe(true);
    await within(section).findByText(/Saved\./, undefined, slow);
  });

  it("asks before a shorter keep-time, queues nothing until yes, and Keep current cancels", async () => {
    const { api, section } = await open("100.cmp1");
    fireEvent.click(within(group(section, "retention")).getByRole("button", { name: "30 days" }));
    expect(await within(section).findByText(w.retentionWarning("30 days"))).toBeTruthy();
    expect(within(section).queryByText(w.saving)).toBeNull();
    fireEvent.click(within(section).getByRole("button", { name: w.keepAsIs }));
    expect(within(section).queryByText(w.retentionWarning("30 days"))).toBeNull();
    expect(within(group(section, "retention")).getByRole("button", { name: "90 days" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(group(section, "retention")).getByRole("button", { name: "30 days" }));
    expect(api.calls.some((c) => c.startsWith("setCampaignSetting"))).toBe(false); // nothing queued before the yes
    fireEvent.click(await within(section).findByRole("button", { name: w.deleteAfter("30 days") }));
    expect(await within(section).findByText(/older than 30 days are deleted every night/, undefined, slow)).toBeTruthy();
    expect(within(group(section, "retention")).getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("does not ask for a longer keep-time", async () => {
    const { section } = await open("100.cmp1");
    fireEvent.click(within(group(section, "retention")).getByRole("button", { name: "Forever" }));
    expect(within(section).queryByText(/will be deleted every night/)).toBeNull();
    await within(section).findByText(/Saved\./, undefined, slow);
  });

  it("shows Discord's refusal in plain text, and a plain error when it goes wrong", async () => {
    const { section } = await open("100.cmp1.cs-refused");
    fireEvent.click(within(group(section, "level")).getByRole("button", { name: "🔇 Quiet" }));
    expect(await within(section).findByText(`${w.refused} That limit is not allowed.`, undefined, slow)).toBeTruthy();
    expect(within(group(section, "level")).getByRole("button", { name: "🔔 Normal" }).getAttribute("aria-pressed")).toBe("true"); // old value stays
    cleanup();
    const failed = await open("100.cmp1.cs-failed");
    fireEvent.click(within(group(failed.section, "level")).getByRole("button", { name: "🔇 Quiet" }));
    expect((await within(failed.section).findByText(w.failed, undefined, slow)).getAttribute("role")).toBe("alert");
  }, 15000);

  it("says the bot will do it when it is back, and keeps the old value showing", async () => {
    const { section } = await open("100.cmp1.cs-offline");
    vi.useFakeTimers({ toFake: ["setTimeout", "Date"] });
    fireEvent.click(within(group(section, "level")).getByRole("button", { name: "🔇 Quiet" }));
    for (let i = 0; i < 50; i += 1) await vi.advanceTimersByTimeAsync(250);
    expect(within(section).getByText(`${w.saving} ${w.slow}`)).toBeTruthy();
    for (let i = 0; i < 100; i += 1) await vi.advanceTimersByTimeAsync(250);
    expect(within(section).getByText(w.offline)).toBeTruthy();
    expect(within(group(section, "level")).getByRole("button", { name: "🔔 Normal" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("what the bot says back", () => {
  it("shows the bot's note once, without Discord's marks, and a plain Saved when it has none", async () => {
    const { section } = await open("100.cmp1");
    fireEvent.click(within(group(section, "retention")).getByRole("button", { name: "1 year" }));
    const note = await within(section).findByText("Saved. Sessions older than 365 days are deleted every night.", undefined, slow);
    expect(note.textContent).not.toMatch(/Saved\. Saved\./);
    fireEvent.click(within(group(section, "level")).getByRole("button", { name: "🔇 Quiet" }));
    await waitFor(() => expect(within(group(section, "level")).getByRole("button", { name: "🔇 Quiet" }).getAttribute("aria-pressed")).toBe("true"), slow);
    expect(within(section).getAllByText(w.saved).length).toBeGreaterThan(0);
  });

  it("lets an old 'chatty' campaign be moved to Normal: neither is pressed", async () => {
    const api = mockApi("table");
    const read = api.campaignPage!.bind(api);
    api.campaignPage = async (r) => {
      const got = await read(r);
      return { ...got, settings: { ...got.settings!, level: "chatty" } };
    };
    render(<CampaignPageView api={api} path="/account/campaigns/100.cmp1" />);
    const section = (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
    const normal = within(group(section, "level")).getByRole("button", { name: "🔔 Normal" });
    expect(normal.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(normal);
    await waitFor(() => expect(normal.getAttribute("aria-pressed")).toBe("true"), slow);
  });
});

describe("who sees what", () => {
  it("shows a player no Settings at all", async () => {
    render(<CampaignPageView api={mockApi("table")} path="/account/campaigns/100.cmp1.cp-player" />);
    await screen.findByRole("heading", { level: 2, name: w.groups.story });
    expect(screen.queryByRole("heading", { name: w.groups.settings })).toBeNull();
    expect(screen.queryByRole("group", { name: w.controls.level })).toBeNull();
  });
});

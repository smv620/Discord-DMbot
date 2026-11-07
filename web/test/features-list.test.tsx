/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "What's switched on" in a campaign's settings (#3693): a read-only list of what the bot wrote, with the
// off ones by name; shown only to a game master, only while its switch is on, and nothing in it can be pressed.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import FeaturesList from "../src/account/FeaturesList";
import { mockApi } from "../src/account/mock";
import { featuresWords as w } from "../src/content/features-list-words";
import type { FeaturesList as Features } from "../src/account/api";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/features-list-switch", () => ({
  get FEATURES_LIST_LIVE() {
    return live.on;
  },
}));

afterEach(cleanup);

const list: Features = {
  writtenAt: 1_790_000_000,
  on: [{ name: "Written recaps", words: "DMbot writes a recap of each session." }],
  off: ["Spoken recaps"],
};

describe("FeaturesList", () => {
  it("lists what is on with its sentence, what is off by name, and the footer", () => {
    render(<FeaturesList features={list} />);
    expect(screen.getByText("Written recaps")).toBeTruthy();
    expect(screen.getByText("DMbot writes a recap of each session.")).toBeTruthy();
    expect(screen.getByText(w.offHeading)).toBeTruthy();
    expect(screen.getByText(/Spoken recaps/)).toBeTruthy();
    expect(screen.getByText(w.footer)).toBeTruthy();
    expect(screen.queryAllByRole("button")).toEqual([]); // read-only: nothing to press
  });

  it("says so when nothing is on, and shows no list of what is off", () => {
    render(<FeaturesList features={{ ...list, on: [] }} />);
    expect(screen.getByText(w.nothingOn)).toBeTruthy();
    expect(screen.queryByText(w.offHeading)).toBeNull();
  });
});

describe("in the campaign settings", () => {
  // (A player's page never carries the list: the server leaves it out, tested in core.)
  const page = async () => {
    const api = mockApi("table");
    return { api, page: await api.campaignPage!("100.cmp1") };
  };

  it("shows the list to a game master while the switch is on", async () => {
    live.on = true;
    const { api, page: p } = await page();
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={p} />);
    expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
  });

  it("shows nothing with the switch off, or without a list from the server", async () => {
    live.on = false;
    const { api, page: p } = await page();
    const { unmount } = render(<CampaignSettings api={api} campaignRef="100.cmp1" page={p} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    unmount();
    live.on = true;
    const { features: _gone, ...without } = p;
    render(<CampaignSettings api={api} campaignRef="100.cmp1" page={without} />);
    await screen.findByRole("heading", { name: /settings/i });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The campaign's chosen world on its Settings group (#5238; core #5231): shown only, as Discord's settings card shows it: the world's name,
// "Not chosen yet", or DMbot's guess waiting for the DM to keep it. An older API sends no `world`, and then the line is not there.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import CampaignSettings from "../src/account/CampaignSettings";
import { mockApi } from "../src/account/mock";
import { campaignPage as w } from "../src/content/campaign-page";

afterEach(() => cleanup());

async function open(world: { id: string; name: string; guessed: boolean } | null | undefined) {
  const api = mockApi("table");
  const page = await api.campaignPage!("100.cmp1");
  const settings = { ...page.settings!, ...(world !== undefined ? { world } : {}) };
  if (world === undefined) delete (settings as { world?: unknown }).world;
  render(<CampaignSettings api={api} campaignRef="100.cmp1" page={{ ...page, settings }} />);
  await screen.findByRole("heading", { name: /settings/i });
}

describe("the world line", () => {
  it("shows a chosen world's name", async () => {
    await open({ id: "w".repeat(32), name: "Faerûn", guessed: false });
    expect(screen.getByText(w.world.label)).toBeTruthy();
    expect(screen.getByText("Faerûn")).toBeTruthy();
    expect(screen.queryByText(w.world.chooseInDiscord)).toBeNull();
    expect(screen.queryByText(w.world.keepInDiscord)).toBeNull();
  });

  it("says not chosen yet when there is none", async () => {
    await open(null);
    expect(screen.getByText(w.world.none)).toBeTruthy();
    expect(screen.getByText("Choose a world in Discord: 🌍 Choose a world on the ⚙️ settings card.")).toBeTruthy();
  });

  it("says DMbot only guessed it, and where to keep it", async () => {
    await open({ id: "w".repeat(32), name: "Faerûn", guessed: true });
    expect(screen.getByText("DMbot guessed Faerûn")).toBeTruthy();
    expect(screen.getByText("Keep it, or choose another, in Discord on the ⚙️ settings card.")).toBeTruthy();
    expect(screen.queryByText(w.world.chooseInDiscord)).toBeNull();
  });

  it("shows nothing from an older API that sends no world", async () => {
    await open(undefined);
    expect(screen.queryByText(w.world.label)).toBeNull();
    expect(screen.queryByText(w.world.none)).toBeNull();
    expect(screen.queryByText(w.world.chooseInDiscord)).toBeNull();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Look up a rule" box is a game master's tool (#5156): a player's campaign page shows no box, and with the switch off a
// game master's page shows none and nothing is asked. The switch is mocked, as campaign-page.test.tsx mocks its own.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/rule-lookup-switch", () => ({
  get RULE_LOOKUP_LIVE() {
    return live.on;
  },
}));

import CampaignPageView from "../src/account/CampaignPage";
import { mockApi } from "../src/account/mock";
import { ruleLookupWords as words } from "../src/content/rule-lookup-words";

afterEach(() => {
  cleanup();
  live.on = true;
});

describe("the Look up a rule box on a campaign's page", () => {
  it("a game master's page shows the box while the switch is on", async () => {
    const api = mockApi("table");
    render(<CampaignPageView api={api} path="/account/campaigns/100.cmp1" />);
    expect(await screen.findByRole("heading", { name: words.heading })).toBeTruthy();
  });

  it("a player's page shows no box, even with the switch on, and asks nothing", async () => {
    const api = mockApi("table");
    const look = vi.fn(api.ruleLookup!.bind(api));
    api.ruleLookup = look;
    render(<CampaignPageView api={api} path="/account/campaigns/100.cmp1.cp-player" />);
    await screen.findByRole("heading", { level: 1 }).catch(() => null);
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
    expect(look).not.toHaveBeenCalled();
  });

  it("with the switch off, a game master's page shows no box and never calls ruleLookup", async () => {
    live.on = false;
    const api = mockApi("table");
    const look = vi.fn(api.ruleLookup!.bind(api));
    api.ruleLookup = look;
    render(<CampaignPageView api={api} path="/account/campaigns/100.cmp1" />);
    await screen.findByRole("heading", { level: 1 }).catch(() => null);
    expect(screen.queryByRole("heading", { name: words.heading })).toBeNull();
    expect(look).not.toHaveBeenCalled();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The game masters section of a campaign's Settings tab (#2768): the list, a person to add found by name, one
// confirmation before a removal, and the words for each way the bot can answer. Shown only while CO_DMS_LIVE is
// true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import CampaignPageView from "../src/account/CampaignPage";
import GameMastersSection from "../src/account/GameMasters";
import { mockApi } from "../src/account/mock";
import { gameMasterWords as w } from "../src/content/game-masters-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/game-masters-switch", () => ({
  get CO_DMS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));
const slow = { timeout: 5000 };

async function section(ref = "100.cmp1") {
  const api = mockApi("table");
  render(<GameMastersSection api={api} campaignRef={ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const person = (name: string) => screen.getByText(name, { selector: "strong" }).closest("li") as HTMLElement;
const add = (name: string) => screen.getByRole("button", { name: w.addLabel(name) });

describe("while it is switched off", () => {
  it("shows no section, keeps the Discord steps and asks for nothing", async () => {
    const api = mockApi("table");
    render(
      <div class="account">
        <CampaignPageView api={api} path="/account/campaigns/100.cmp1" />
      </div>,
    );
    await screen.findByRole("heading", { level: 2, name: "Settings" });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(screen.getAllByText(/Campaign management → 👑 Game masters/).length).toBeGreaterThan(0);
    expect(calls(api, "campaignGameMasters")).toHaveLength(0);
  });

  it("shows the section once it is on, and drops the Discord steps for the list", async () => {
    live.on = true;
    const api = mockApi("table");
    render(
      <div class="account">
        <CampaignPageView api={api} path="/account/campaigns/100.cmp1" />
      </div>,
    );
    expect(await screen.findByRole("heading", { name: w.heading }, slow)).toBeTruthy();
    expect(calls(api, "campaignGameMasters")).toEqual(["campaignGameMasters:100.cmp1"]);
    expect(screen.queryByText(/Campaign management → 👑 Game masters/)).toBeNull();
  });
});

describe("the list", () => {
  it("shows the plan holder and you, with no way to remove the plan holder", async () => {
    await section();
    const me = person("Belleros");
    expect(within(me).getByText(`(${w.plan})`)).toBeTruthy();
    expect(within(me).getByText(`(${w.you})`)).toBeTruthy();
    expect(within(me).getByText(w.planNoteSelf)).toBeTruthy(); // it is you: "Your plan runs this campaign"
    expect(within(me).queryByRole("button")).toBeNull();
  });

  it("says when it could not be loaded", async () => {
    const api = mockApi("table");
    api.campaignGameMasters = async () => {
      throw new ApiError("network");
    };
    render(<GameMastersSection api={api} campaignRef="100.cmp1" />);
    expect((await screen.findByRole("alert")).textContent).toBe(w.failedToLoad);
  });
});

describe("adding", () => {
  it("offers the members, finds one by name, and sends the add through the queue", async () => {
    const api = await section();
    expect(await screen.findByText("Dana", { selector: "strong" })).toBeTruthy();
    fireEvent.input(screen.getByLabelText(w.searchLabel), { target: { value: "fay" } });
    await waitFor(() => expect(screen.queryByText("Dana", { selector: "strong" })).toBeNull());
    fireEvent.click(add("Fay Merrow"));
    expect(within(person("Fay Merrow")).getByText(w.stage["waiting"] as string)).toBeTruthy();
    await waitFor(() => expect(within(person("Fay Merrow")).getByText("Fay Merrow is now a game master.")).toBeTruthy(), slow);
    expect(calls(api, "setCampaignSetting")).toEqual(["setCampaignSetting:100.cmp1:co_dm:200000000000000003:on"]);
  });

  it("shows the new game master in the list afterwards, and no longer offers them", async () => {
    await section();
    fireEvent.click(await screen.findByRole("button", { name: w.addLabel("Dana") }));
    await waitFor(() => expect(screen.getAllByText("Dana", { selector: "strong" })).toHaveLength(1), slow);
    await waitFor(() => expect(screen.queryByRole("button", { name: w.addLabel("Dana") })).toBeNull(), slow);
    expect(within(person("Dana")).getByRole("button", { name: w.removeLabel("Dana") })).toBeTruthy();
  });

  it("says so when nobody can be offered, and where to go instead", async () => {
    await section("100.cmp1.gm-nobody");
    expect(await screen.findByText(w.nobody)).toBeTruthy();
  });

  it("shows no search at the limit, and says why", async () => {
    await section("100.cmp1.gm-full");
    expect(screen.getByText(w.full(5))).toBeTruthy();
    expect(screen.queryByLabelText(w.searchLabel)).toBeNull();
  });

  it("shows Discord's own words when the bot refuses, and leaves the list as it is", async () => {
    const api = await section();
    api.campaignSettingAnswer = async () => ({
      state: "failed",
      error: "refused",
      setting: "co_dm",
      value: "200000000000000001",
      note: "**Dana** is already a game master.",
    });
    fireEvent.click(await screen.findByRole("button", { name: w.addLabel("Dana") }));
    await waitFor(() => expect(within(person("Dana")).getByRole("alert").textContent).toBe("Dana is already a game master."), slow);
  });

  it("says when the bot could not save it", async () => {
    await section("100.cmp1.gm-failed");
    fireEvent.click(await screen.findByRole("button", { name: w.addLabel("Dana") }));
    await waitFor(() => expect(within(person("Dana")).getByRole("alert").textContent).toBe(w.problems["server"]), slow);
  });

  it("ignores a second press while it is saving", async () => {
    const api = await section();
    const button = await screen.findByRole("button", { name: w.addLabel("Dana") });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(within(person("Dana")).getByText("Dana is now a game master.")).toBeTruthy(), slow);
    expect(calls(api, "setCampaignSetting")).toHaveLength(1);
  });

  it("says what is wrong when the call itself is refused", async () => {
    const api = await section();
    api.setCampaignSetting = async () => {
      throw new ApiError("not-allowed");
    };
    fireEvent.click(await screen.findByRole("button", { name: w.addLabel("Dana") }));
    await waitFor(() => expect(within(person("Dana")).getByRole("alert").textContent).toBe(w.problems["not-allowed"]));
  });
});

describe("removing", () => {
  async function withDana() {
    const api = await section();
    fireEvent.click(await screen.findByRole("button", { name: w.addLabel("Dana") }));
    await waitFor(() => expect(screen.queryByRole("button", { name: w.addLabel("Dana") })).toBeNull(), slow);
    return api;
  }

  it("asks once, and Cancel changes nothing", async () => {
    const api = await withDana();
    fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Dana") }));
    expect(screen.getByText(w.confirm("Dana"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.cancel }));
    expect(screen.queryByText(w.confirm("Dana"))).toBeNull();
    expect(calls(api, "setCampaignSetting")).toHaveLength(1); // only the add
  });

  it("sends the removal after the confirmation and drops them from the list", async () => {
    const api = await withDana();
    fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Dana") }));
    fireEvent.click(within(screen.getByRole("group", { name: w.removeLabel("Dana") })).getByRole("button", { name: w.confirmYes }));
    await waitFor(() => expect(calls(api, "setCampaignSetting")).toContain("setCampaignSetting:100.cmp1:co_dm:200000000000000001:off"), slow);
    await waitFor(() => expect(screen.getByText("Dana is no longer a game master.")).toBeTruthy(), slow);
  });
});

it("says so when a game master who removed themselves can no longer read the list", async () => {
  const api = mockApi("table");
  let reads = 0;
  api.campaignGameMasters = async () => {
    if (reads++ > 0) throw new ApiError("not-allowed"); // after the removal the campaign is no longer theirs
    return {
      max: 5,
      gameMasters: [
        { userId: "1", name: "Belleros", plan: true, you: false },
        { userId: "100000000000000009", name: "Pat", plan: false, you: true },
      ],
    };
  };
  api.campaignSettingAnswer = async () => ({ state: "done", error: "", setting: "co_dm", value: "100000000000000009", note: "" });
  render(<GameMastersSection api={api} campaignRef="100.cmp1" />);
  await screen.findByRole("heading", { name: w.heading });
  fireEvent.click(screen.getByRole("button", { name: w.removeLabel("Pat") }));
  expect(screen.getByText(w.confirmSelf)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: w.confirmYes }));
  expect(await screen.findByText(w.left, {}, slow)).toBeTruthy();
  expect(screen.queryByRole("button", { name: w.removeLabel("Pat") })).toBeNull();
});

it("has no serious accessibility problem", async () => {
  await section();
  await screen.findByText("Dana", { selector: "strong" });
  const result = await axe.run(document.body, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }, rules: { "color-contrast": { enabled: false } } });
  expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
});

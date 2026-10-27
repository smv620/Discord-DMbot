/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The optional rules section of a campaign's Settings tab (#2753): the list the API gives, a switch that goes
// through the settings queue and waits for the bot's answer, and the words for each way it can go. Shown only
// while OPTIONAL_RULES_LIVE is true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import CampaignPageView from "../src/account/CampaignPage";
import { mockApi } from "../src/account/mock";
import OptionalRulesSection from "../src/account/OptionalRules";
import { optionalWords as w } from "../src/content/optional-rules-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/optional-rules-switch", () => ({
  get OPTIONAL_RULES_LIVE() {
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
  render(<OptionalRulesSection api={api} campaignRef={ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}
const rule = (name: string) => screen.getByText(name, { selector: "strong" }).closest("li") as HTMLElement;

describe("while it is switched off", () => {
  it("shows no section on the Settings tab and asks for no rules", async () => {
    const api = mockApi("table");
    render(
      <div class="account">
        <CampaignPageView api={api} path="/account/campaigns/100.cmp1" />
      </div>,
    );
    await screen.findByRole("heading", { level: 2, name: "Settings" });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(calls(api, "campaignOptionalRules")).toHaveLength(0);
  });

  it("shows the section on the Settings tab once it is on", async () => {
    live.on = true;
    const api = mockApi("table");
    render(
      <div class="account">
        <CampaignPageView api={api} path="/account/campaigns/100.cmp1" />
      </div>,
    );
    expect(await screen.findByRole("heading", { name: w.heading }, slow)).toBeTruthy();
    expect(calls(api, "campaignOptionalRules")).toEqual(["campaignOptionalRules:100.cmp1"]);
  });
});

describe("the list", () => {
  it("shows every rule by book, with what it does and whether it is on", async () => {
    await section();
    expect(screen.getByText("A Pretend Guide")).toBeTruthy();
    expect(screen.getByText("Another Pretend Book")).toBeTruthy();
    const brittle = rule("Brittle bones");
    expect(within(brittle).getByText(/A hard fall can break a bone/)).toBeTruthy();
    const sw = within(brittle).getByRole("switch", { name: w.turnOff("Brittle bones") });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(within(brittle).getByText(w.followsDefault)).toBeTruthy();
    expect(screen.getByText(w.disclaimer)).toBeTruthy();
  });

  it("says plainly when there are none to offer", async () => {
    await section("100.cmp1.or-none");
    expect(screen.getByText(w.none("2024 rules"))).toBeTruthy();
  });

  it("says when it could not be loaded", async () => {
    const api = mockApi("table");
    api.campaignOptionalRules = async () => {
      throw new ApiError("network");
    };
    render(<OptionalRulesSection api={api} campaignRef="100.cmp1" />);
    expect((await screen.findByRole("alert")).textContent).toBe(w.failedToLoad);
  });
});

describe("switching a rule", () => {
  it("sends the choice through the queue, waits for the bot, then shows it", async () => {
    const api = await section();
    fireEvent.click(within(rule("Brittle bones")).getByRole("switch"));
    expect(within(rule("Brittle bones")).getByText(w.saving)).toBeTruthy();
    expect(await within(rule("Brittle bones")).findByText("Brittle bones is now off.", {}, slow)).toBeTruthy();
    const sw = within(rule("Brittle bones")).getByRole("switch", { name: w.turnOn("Brittle bones") });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(within(rule("Brittle bones")).getByText(w.chosen)).toBeTruthy();
    expect(calls(api, "setCampaignSetting")).toEqual(["setCampaignSetting:100.cmp1:optional_rule:pretend-bones:off"]);
    // Other rules are left as they were.
    expect(within(rule("Carts and loads")).getByRole("switch").getAttribute("aria-checked")).toBe("true");
  });

  it("ignores a second press while it is saving", async () => {
    const api = await section();
    const sw = within(rule("Brittle bones")).getByRole("switch") as HTMLButtonElement;
    fireEvent.click(sw);
    fireEvent.click(sw);
    await within(rule("Brittle bones")).findByText("Brittle bones is now off.", {}, slow);
    expect(calls(api, "setCampaignSetting")).toHaveLength(1);
  });

  it("says when the bot could not save it, and leaves the switch as it was", async () => {
    await section("100.cmp1.or-failed");
    fireEvent.click(within(rule("Brittle bones")).getByRole("switch"));
    await waitFor(() => expect(within(rule("Brittle bones")).getByRole("alert").textContent).toBe(w.problems["server"]), slow);
    expect(within(rule("Brittle bones")).getByRole("switch").getAttribute("aria-checked")).toBe("true");
  });

  it("says what is wrong when the call is refused", async () => {
    const api = await section();
    api.setCampaignSetting = async () => {
      throw new ApiError("not-allowed");
    };
    fireEvent.click(within(rule("Brittle bones")).getByRole("switch"));
    await waitFor(() => expect(within(rule("Brittle bones")).getByRole("alert").textContent).toBe(w.problems["not-allowed"]));
  });

  it("has no serious accessibility problem", async () => {
    await section();
    const result = await axe.run(document.body, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }, rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

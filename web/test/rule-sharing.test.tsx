/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Share and follow" section of a campaign's House rules page (#2783): publish, stop sharing, follow from the
// list or by a code (look first, then follow), stop following, and the words for each way the bot can answer. It is
// shown only while RULE_SHARING_LIVE is true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import HouseRulesPage from "../src/account/HouseRules";
import { mockApi } from "../src/account/mock";
import RuleSharingSection from "../src/account/RuleSharing";
import { ruleSharingWords as w } from "../src/content/rule-sharing-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/rule-sharing-switch", () => ({
  get RULE_SHARING_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));
const slow = { timeout: 8000 };
let count = 0;
/** A fresh pretend campaign each time: the pretend API remembers what was shared, per campaign. */
async function section(ending = "") {
  const ref = `100.rs${(count += 1)}${ending}`;
  const api = mockApi("table");
  render(<RuleSharingSection api={api} campaignRef={ref} />);
  await screen.findByRole("heading", { name: w.heading });
  return { api, ref };
}
const button = (name: string | RegExp) => screen.getByRole("button", { name });
/** Type a name, press Share, and answer the question with yes. */
function share(name: string) {
  fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: name } });
  fireEvent.click(button(w.share));
  fireEvent.click(button(w.confirmShareYes));
}

describe("while it is switched off", () => {
  it("shows no section and asks for nothing", async () => {
    const api = mockApi("table");
    render(
      <div class="account">
        <HouseRulesPage api={api} path="/account/house-rules/200000000000000001.cmp1" />
      </div>,
    );
    await screen.findByRole("heading", { level: 2 });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    expect(calls(api, "ruleSharing")).toHaveLength(0);
  });

  it("shows the section on the House rules page once it is on", async () => {
    live.on = true;
    const api = mockApi("table");
    render(
      <div class="account">
        <HouseRulesPage api={api} path="/account/house-rules/200000000000000001.cmp1" />
      </div>,
    );
    expect(await screen.findByRole("heading", { name: w.heading }, slow)).toBeTruthy();
    expect(calls(api, "ruleSharing:")).toEqual(["ruleSharing:200000000000000001.cmp1"]);
  });
});

describe("who sees it", () => {
  it("shows nothing at all to a player", async () => {
    const api = mockApi("table");
    const { container } = render(<RuleSharingSection api={api} campaignRef="100.rs.rs-player" />);
    await waitFor(() => expect(calls(api, "ruleSharing:")).toHaveLength(1));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("sharing your rules", () => {
  it("asks for a name, queues the choice, shows the wait and says it is shared", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: "  House Rules of the North " } });
    fireEvent.click(button(w.share));
    expect(screen.getByText(w.confirmShare("House Rules of the North", 3))).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0); // nothing is sent until the yes
    fireEvent.click(button(w.confirmShareYes));
    expect(await screen.findByText(w.stage["waiting"] as string)).toBeTruthy();
    expect(await screen.findByText("Shared as House Rules of the North.", {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toEqual([expect.stringMatching(/:publish:House Rules of the North$/)]);
    expect(await screen.findByText(w.sharedAs("House Rules of the North", 1))).toBeTruthy();
    expect(screen.queryByLabelText(w.nameLabel)).toBeNull();
  });

  it("does not ask, or send, with an empty name, and says a name is needed", async () => {
    const { api } = await section();
    expect(screen.getByText(w.nameFirst)).toBeTruthy();
    fireEvent.click(button(w.share));
    expect(screen.queryByRole("button", { name: w.confirmShareYes })).toBeNull();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0);
  });

  it("sends nothing when the question is answered no", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: "Rules" } });
    fireEvent.click(button(w.share));
    fireEvent.click(button(w.cancel));
    expect(screen.queryByRole("button", { name: w.confirmShareYes })).toBeNull();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0);
  });

  it("sends one choice for a double press", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.nameLabel), { target: { value: "Rules" } });
    fireEvent.click(button(w.share));
    const yes = button(w.confirmShareYes);
    fireEvent.click(yes);
    fireEvent.click(yes);
    await screen.findByText(w.sharedAs("Rules", 1), {}, slow);
    expect(calls(api, "chooseRuleSharing")).toHaveLength(1);
  });

  it("says a plan is needed when the plan does not allow it", async () => {
    await section(".rs-free");
    expect(screen.getByText(w.plan)).toBeTruthy();
    expect(screen.queryByLabelText(w.nameLabel)).toBeNull();
  });

  it("says there is nothing to share without a house rule", async () => {
    await section(".rs-norules");
    expect(screen.getByText(w.noRules)).toBeTruthy();
    expect(screen.queryByLabelText(w.nameLabel)).toBeNull();
  });

  it("stops sharing, and says followers keep their copy", async () => {
    const { api } = await section();
    share("Rules");
    await screen.findByText(w.sharedAs("Rules", 1), {}, slow);
    expect(screen.getByText(w.stopNote)).toBeTruthy();
    fireEvent.click(button(w.stop));
    expect(screen.getByText(w.confirmStop("Rules"))).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(1); // only the publish so far
    fireEvent.click(button(w.confirmStopYes));
    expect(await screen.findByText(/Stopped sharing/, {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toEqual([expect.stringMatching(/:publish:Rules$/), expect.stringMatching(/:stop_sharing$/)]);
    expect(await screen.findByText(w.notShared("Rules"))).toBeTruthy();
  });

  it("never shows a share code and points to Discord for one", async () => {
    const { api } = await section();
    share("Rules");
    await screen.findByText(w.sharedAs("Rules", 1), {}, slow);
    expect(screen.getByText(w.codesInDiscord)).toBeTruthy();
    // The real path in Discord, as Supervisor named it on #2796.
    expect(w.codesInDiscord).toContain("⚙️ Manage DMbot → 📚 Shared rules → 🔑 New share code");
    expect(w.codeHelp).toContain("⚙️ Manage DMbot → 📚 Shared rules → 🔑 New share code");
    expect(document.body.textContent).not.toMatch(/ABCDEFGHJK/);
    expect(calls(api, "chooseRuleSharing").some((c) => /make_code|turn_off_code/.test(c))).toBe(false);
  });
});

describe("following", () => {
  it("lists the sets shared here, asks once, then follows and offers to stop", async () => {
    const { api } = await section();
    fireEvent.click(button(w.followLabel("Frostfell Table Rules")));
    expect(screen.getByText(w.confirmFollow("Frostfell Table Rules", 4))).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0); // nothing is sent until the yes
    fireEvent.click(button(w.confirmYes));
    expect(await screen.findByText("Now following Frostfell Table Rules.", {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toEqual([expect.stringMatching(/:follow_here:b{32}$/)]);
    expect(screen.getByText(w.following("Frostfell Table Rules", "The Winter Court", 3))).toBeTruthy();
    expect(screen.getByText(w.followingOne)).toBeTruthy();
    expect(screen.queryByText(w.hereHeading)).toBeNull(); // one set at a time
  });

  it("changes nothing on Cancel", async () => {
    const { api } = await section();
    fireEvent.click(button(w.followLabel("Quick Combat")));
    fireEvent.click(button(w.cancel));
    expect(screen.queryByText(w.confirmFollow("Quick Combat", 2))).toBeNull();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0);
  });

  it("looks a code up first, shows what would be copied, and follows only on the second press", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "abcde-fghjk" } });
    fireEvent.click(button(w.look));
    expect(await screen.findByText(/Found Frostfell Table Rules/, {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toEqual([expect.stringMatching(/:look_code:ABCDEFGHJK$/)]);
    expect(calls(api, "chooseRuleSharing").some((c) => /follow/.test(c))).toBe(false);
    fireEvent.click(button(w.foundFollow));
    expect(await screen.findByText("Now following Frostfell Table Rules.", {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")[1]).toMatch(/:follow_code:ABCDEFGHJK$/);
  });

  it("lets go of a found set with Not these, sending nothing more", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "ABCDEFGHJK" } });
    fireEvent.click(button(w.look));
    await screen.findByText(/Found Frostfell Table Rules/, {}, slow);
    fireEvent.click(button(w.foundCancel));
    expect(screen.queryByRole("button", { name: w.foundFollow })).toBeNull();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(1);
  });

  it("does not look up a code that is not ten letters and numbers", async () => {
    const { api } = await section();
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "SHORT" } });
    fireEvent.click(button(w.look));
    expect(calls(api, "chooseRuleSharing")).toHaveLength(0);
  });

  it("drops the found set when the code is edited", async () => {
    await section();
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "ABCDEFGHJK" } });
    fireEvent.click(button(w.look));
    await screen.findByText(/Found Frostfell Table Rules/, {}, slow);
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "ABCDEFGHJ" } });
    expect(screen.queryByRole("button", { name: w.foundFollow })).toBeNull();
  });

  it("stops following", async () => {
    const { api } = await section();
    fireEvent.click(button(w.followLabel("Quick Combat")));
    fireEvent.click(button(w.confirmYes));
    await screen.findByText(/Now following Quick Combat/, {}, slow);
    fireEvent.click(button(w.stopFollowingLabel("Quick Combat")));
    expect(screen.getByText(w.confirmUnfollow("Quick Combat"))).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")).toHaveLength(1); // only the follow so far
    fireEvent.click(button(w.confirmUnfollowYes));
    expect(await screen.findByText(/Stopped following/, {}, slow)).toBeTruthy();
    expect(calls(api, "chooseRuleSharing")[1]).toMatch(/:unfollow$/);
    expect(await screen.findByText(w.hereHeading)).toBeTruthy();
  });
});

describe("when the bot cannot", () => {
  it("shows Discord's own words for a refusal and leaves the page as it was", async () => {
    await section(".rs-failed");
    fireEvent.input(screen.getByLabelText(w.codeLabel), { target: { value: "ZZZZZZZZZZ" } });
    fireEvent.click(button(w.look));
    const alert = await screen.findByText("That code doesn't match a set. Check it and try again.", {}, slow);
    expect(alert.getAttribute("role")).toBe("alert");
    expect(screen.queryByRole("button", { name: w.foundFollow })).toBeNull();
  });

  it("keeps showing the wait, with what to do, when the bot is slow", async () => {
    await section(".rs-offline");
    share("Rules");
    expect(await screen.findByText(w.stage["waiting"] as string)).toBeTruthy();
    expect(screen.queryByText(w.sharedAs("Rules", 1))).toBeNull();
  });
});

describe("accessibility and words", () => {
  it("has no accessibility violations", async () => {
    await section();
    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("uses no technical words", async () => {
    const { container } = render(<RuleSharingSection api={mockApi("table")} campaignRef="100.rs.words" />);
    await screen.findByRole("heading", { name: w.heading });
    const text = (container.textContent ?? "") + JSON.stringify(w);
    expect(text).not.toMatch(/\b(queue|API|database|endpoint|JSON|ruleset)\b/i);
    expect(within(container as HTMLElement).getAllByRole("button").length).toBeGreaterThan(0);
  });
});

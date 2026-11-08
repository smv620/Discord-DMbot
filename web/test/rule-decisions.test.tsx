/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The decided pairs of house rules (#3697, behind RULE_CLASHES_LIVE): which of two rules that disagree wins, as the DM
// decided, with the rules' own words; "Rule N wins instead" and "Clear this choice" through the sharing queue, own rules
// only; a pair with a followed set's rule is read-only; and nothing at all while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import RuleSharingSection from "../src/account/RuleSharing";
import { ruleSharingWords as w } from "../src/content/rule-sharing-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/rule-clashes-switch", () => ({
  get RULE_CLASHES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const sets = [{ name: "Red Table", version: 4, cite: "Red Table house rules, v4", rules: [{ number: 1, rule: "Inspiration stacks", supersedes: null }] }];
const rules = [
  { number: 2, rule: "Potions are a bonus action", supersedes: null, fromSet: null },
  { number: 1, rule: "Crits double the dice", supersedes: null, fromSet: null },
];
let count = 0;
async function open(ending = "") {
  const ref = `100.rd${(count += 1)}${ending}`;
  const api = mockApi("table");
  render(<RuleSharingSection api={api} campaignRef={ref} rules={rules} sets={sets} />);
  await screen.findByRole("heading", { name: w.clashHeading });
  return { api, ref };
}

describe("the decided pairs", () => {
  it("lists each pair with the rules' words and says who wins", async () => {
    await open();
    expect(await screen.findByText(w.clashWins("Rule 2", "Rule 1"))).toBeTruthy();
    expect(screen.getAllByText("Rule 2: Potions are a bonus action").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rule 1: Crits double the dice").length).toBeGreaterThan(0);
  });

  it("shows a pair with a followed set's rule read-only, with a pointer to Discord", async () => {
    await open();
    await screen.findByText(w.clashWins("Rule 1 of Red Table", "Rule 2"));
    expect(screen.getAllByText(w.clashSetNote)).toHaveLength(1);
    // (only the own pair has buttons)
    expect(screen.getAllByRole("button", { name: /^Clear this choice/ })).toHaveLength(1);
  });

  it("shows a followed set's rule words, and an own rule's words only from the own rules", async () => {
    const api = mockApi("table");
    // (a followed set's rule 2 shares a number with own rule 2: the own words must not be taken from it)
    const tricky = [...rules, { number: 2, rule: "Set's own rule 2", supersedes: null, fromSet: "Red Table" }];
    render(<RuleSharingSection api={api} campaignRef="100.rd-words" rules={tricky} sets={sets} />);
    await screen.findByText(w.clashWins("Rule 1 of Red Table", "Rule 2"));
    expect(screen.getAllByText("Rule 1 of Red Table: Inspiration stacks").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Set's own rule 2/)).toBeNull();
    expect(screen.getAllByText("Rule 2: Potions are a bonus action").length).toBeGreaterThan(0);
  });

  it("reads the list again when the bot refuses a change, and says why", async () => {
    const { api, ref } = await open(".rs-failed");
    await screen.findByText(w.clashWins("Rule 2", "Rule 1"));
    const before = api.calls.filter((c) => c.startsWith("ruleDecisions")).length;
    fireEvent.click(screen.getByRole("button", { name: w.clashFlip(1) }));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("ruleDecisions")).length).toBeGreaterThan(before), { timeout: 8000 });
    expect(ref).toContain("rs-failed");
    expect((await screen.findAllByRole("alert", {}, { timeout: 8000 })).some((a) => (a.textContent ?? "") !== "")).toBe(true);
  });

  it("says when nothing is decided yet", async () => {
    await open(".rd-none");
    expect(await screen.findByText(w.clashNone)).toBeTruthy();
  });

  it("changes the winner: sends W:L through the sharing queue, then reads the list again", async () => {
    const { api } = await open();
    await screen.findByText(w.clashWins("Rule 2", "Rule 1"));
    fireEvent.click(screen.getByRole("button", { name: w.clashFlip(1) }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith("decide_clash:1:2"))).toBe(true), { timeout: 8000 });
    expect(await screen.findByText(w.clashWins("Rule 1", "Rule 2"), {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("ruleDecisions")).length).toBeGreaterThan(1);
  });

  it("clears a choice: sends A:B, and the pair leaves the list", async () => {
    const { api } = await open();
    await screen.findByText(w.clashWins("Rule 2", "Rule 1"));
    fireEvent.click(screen.getByRole("button", { name: w.clashClearFor(w.clashWins("Rule 2", "Rule 1")) }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith("clear_clash:1:2"))).toBe(true), { timeout: 8000 });
    await waitFor(() => expect(screen.queryByText(w.clashWins("Rule 2", "Rule 1"))).toBeNull(), { timeout: 8000 });
  });

  it("asks for nothing and shows nothing while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    render(<RuleSharingSection api={api} campaignRef="100.rd-off" rules={rules} sets={sets} />);
    await screen.findByRole("heading", { name: w.heading });
    expect(screen.queryByRole("heading", { name: w.clashHeading })).toBeNull();
    expect(api.calls.filter((c) => c.startsWith("ruleDecisions"))).toEqual([]);
  });

  it("shows nothing to someone who isn't a game master", async () => {
    const api = mockApi("table");
    render(<RuleSharingSection api={api} campaignRef="100.rd.rs-player" rules={rules} sets={sets} />);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("ruleSharing"))).toBe(true));
    expect(screen.queryByText(w.clashLead)).toBeNull();
    expect(screen.queryByRole("heading", { name: w.clashHeading })).toBeNull();
  });
});

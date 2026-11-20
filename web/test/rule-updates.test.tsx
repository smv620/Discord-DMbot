/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "See changes" under a followed house-rules set (#4841 C1, #4845): what a newer version would change, shown as core gives it,
// and nothing taken. The card is tested on its own; the House rules page only mounts it while RULE_UPDATES_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, RuleUpdate } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RuleUpdateCard from "../src/account/RuleUpdate";

vi.mock("../src/content/rule-updates-switch", () => ({ RULE_UPDATES_LIVE: true }));

afterEach(() => {
  cleanup();
});

const REF = "100.cmp1";
const waiting: RuleUpdate = {
  name: "Tavern rules",
  publisher: "The Lantern table",
  from: 2,
  to: 3,
  words: "3 rules and 1 relationship",
  lines: ["Rule 2 now says a short rest is one hour.", "A new rule on torches.", "Rule 5 is gone."],
  more: 2,
};

/** An API whose ruleUpdate answers with `update` (or fails), and records the ref it was asked for. */
function apiWith(answer: { update: RuleUpdate | null } | "fail"): { api: AccountApi; asked: string[] } {
  const asked: string[] = [];
  const api = mockApi("table");
  api.ruleUpdate = async (ref) => {
    asked.push(ref);
    if (answer === "fail") throw new Error("offline");
    return answer;
  };
  return { api, asked };
}

describe("See changes under a followed set (#4845)", () => {
  it("says a newer version is waiting, that DMbot takes it at the next session, and offers See changes", async () => {
    const { api } = apiWith({ update: waiting });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect(await screen.findByText("A newer version of Tavern rules is waiting: version 3 (you follow version 2).")).toBeTruthy();
    expect(screen.getByText("DMbot takes this update when your next session starts, never during a game.")).toBeTruthy();
    const button = screen.getByRole("button", { name: "See changes" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  it("See changes opens the lines and the more line; Hide changes closes them", async () => {
    const { api } = apiWith({ update: waiting });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "See changes" }));
    expect(screen.getByText("It changes 3 rules and 1 relationship:")).toBeTruthy();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Rule 2 now says a short rest is one hour.",
      "A new rule on torches.",
      "Rule 5 is gone.",
    ]);
    expect(screen.getByText("…and 2 more.")).toBeTruthy();
    const hide = screen.getByRole("button", { name: "Hide changes" });
    expect(hide.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(hide);
    expect(screen.queryByText("It changes 3 rules and 1 relationship:")).toBeNull();
    expect(screen.getByRole("button", { name: "See changes" })).toBeTruthy();
  });

  it("leaves out the more line when nothing is left over", async () => {
    const { api } = apiWith({ update: { ...waiting, more: 0 } });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "See changes" }));
    expect(screen.queryByText(/more\.$/)).toBeNull();
  });

  it("draws nothing when no update waits", async () => {
    const { api, asked } = apiWith({ update: null });
    const { container } = render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    await waitFor(() => expect(asked).toEqual([REF]));
    await Promise.resolve();
    expect(container.textContent).toBe("");
  });

  it("draws nothing when the waiting update is for another set", async () => {
    const { api, asked } = apiWith({ update: waiting });
    const { container } = render(<RuleUpdateCard api={api} campaignRef={REF} name="Lantern rules" />);
    await waitFor(() => expect(asked).toEqual([REF]));
    await Promise.resolve();
    expect(container.textContent).toBe("");
  });

  it("says so, in an alert, when the read fails", async () => {
    const { api } = apiWith("fail");
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the changes.");
  });

  it("asks the mock by its route name and shows the mock's waiting update", async () => {
    const api = mockApi("table");
    render(<RuleUpdateCard api={api} campaignRef={`${REF}.ru-update`} name="Tavern rules" />);
    expect(await screen.findByText("A newer version of Tavern rules is waiting: version 3 (you follow version 2).")).toBeTruthy();
    expect(api.calls).toContain(`ruleUpdate:${REF}.ru-update`);
  });
});

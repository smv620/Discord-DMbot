/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1, the page half of #4872): a newer version
// waiting, or an update DMbot already took, with what changed. The card is tested on its own; the Shared rules page only mounts it
// while RULE_UPDATES_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, PackUpdateItem } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import PackUpdatesCard from "../src/account/PackUpdates";

afterEach(() => {
  cleanup();
});

const REF = "100.cmp1";
const lantern: PackUpdateItem = {
  setId: "s1",
  name: "Lantern Tavern pack",
  publisher: "The Lantern table",
  from: 1,
  to: 2,
  words: "2 rules",
  lines: ["Rule 1 now says a torch lasts an hour.", "A new rule on rope."],
  more: 0,
};
const moor: PackUpdateItem = { setId: "s2", name: "Moor Road pack", publisher: "The Moor table", from: 3, to: 4, words: "1 rule", lines: ["Rule 1 is new."], more: 0 };

/** An API that answers the packs as given, or fails. */
function packsApi(answer: { waiting: PackUpdateItem[]; taken: PackUpdateItem[] } | "fail"): AccountApi {
  const api = mockApi("table");
  api.packUpdates = async () => {
    if (answer === "fail") throw new Error("offline");
    return answer;
  };
  return api;
}

describe("packs a campaign follows from other tables (#4872)", () => {
  it("a waiting pack says so, that DMbot takes it at the next session, and See changes opens its lines", async () => {
    render(<PackUpdatesCard api={packsApi({ waiting: [lantern], taken: [] })} campaignRef={REF} />);
    expect(await screen.findByText("Lantern Tavern pack has a newer version waiting: version 2 (you follow version 1).")).toBeTruthy();
    expect(screen.getByText("DMbot takes this update when your next session starts, never during a game.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "See changes" }));
    expect(screen.getByText("It changes 2 rules:")).toBeTruthy();
    expect(screen.getByText("Rule 1 now says a torch lasts an hour.")).toBeTruthy();
    expect(screen.getByText("A new rule on rope.")).toBeTruthy();
  });

  it("a pack DMbot took says so, with its changes and no Undo here yet", async () => {
    render(<PackUpdatesCard api={packsApi({ waiting: [], taken: [moor] })} campaignRef={REF} />);
    expect(await screen.findByText("Moor Road pack was updated to version 4. DMbot took the new version.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "See changes" }));
    expect(screen.getByText("Changes:")).toBeTruthy();
    expect(screen.getByText("Rule 1 is new.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });

  it("shows two packs, the waiting one and the taken one", async () => {
    render(<PackUpdatesCard api={packsApi({ waiting: [lantern], taken: [moor] })} campaignRef={REF} />);
    expect(await screen.findByText("Lantern Tavern pack has a newer version waiting: version 2 (you follow version 1).")).toBeTruthy();
    expect(screen.getByText("Moor Road pack was updated to version 4. DMbot took the new version.")).toBeTruthy();
  });

  it("a pack with a newer version waiting hides its taken update", async () => {
    const taken = { ...lantern, to: 2 };
    render(<PackUpdatesCard api={packsApi({ waiting: [lantern], taken: [taken] })} campaignRef={REF} />);
    expect(await screen.findByText("Lantern Tavern pack has a newer version waiting: version 2 (you follow version 1).")).toBeTruthy();
    expect(screen.queryByText("Lantern Tavern pack was updated to version 2. DMbot took the new version.")).toBeNull();
  });

  it("draws nothing when no pack waits and none was taken", async () => {
    const { container } = render(<PackUpdatesCard api={packsApi({ waiting: [], taken: [] })} campaignRef={REF} />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("says so, in an alert, when the read fails", async () => {
    render(<PackUpdatesCard api={packsApi("fail")} campaignRef={REF} />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the changes.");
  });

  it("asks the mock by name, and the mock's packs show", async () => {
    const api = mockApi("table");
    render(<PackUpdatesCard api={api} campaignRef={`${REF}.pk-updates`} />);
    expect(await screen.findByText("Lantern Tavern pack has a newer version waiting: version 2 (you follow version 1).")).toBeTruthy();
    expect(screen.getByText("Moor Road pack was updated to version 4. DMbot took the new version.")).toBeTruthy();
    expect(api.calls).toContain(`packUpdates:${REF}.pk-updates`);
  });
});

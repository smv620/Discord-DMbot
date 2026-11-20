/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1, the page half of #4872): a newer version
// waiting, or an update DMbot already took, with what changed. The card is tested on its own; the Shared rules page only mounts it
// while RULE_UPDATES_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import namesArea from "../src/account/api-names";
import { ApiError, type AccountApi, type ApiHelpers, errorKinds, type PackUpdateItem } from "../src/account/api";
import SharingPage from "../src/account/Sharing";
import { mockApi } from "../src/account/mock";
import PackUpdatesCard from "../src/account/PackUpdates";

// The Shared rules page mounts the card only while RULE_UPDATES_LIVE is on; the gate test turns it off (#4872).
const gate = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/rule-updates-switch", () => ({
  get RULE_UPDATES_LIVE() {
    return gate.on;
  },
}));

afterEach(() => {
  cleanup();
  gate.on = true;
});

const moor2: PackUpdateItem = { ...moor0(), setId: "s3", name: "Harbor pack", from: 5, to: 6, lines: ["Rule 2 is new."] };
function moor0(): PackUpdateItem {
  return { setId: "s2", name: "Moor Road pack", publisher: "The Moor table", from: 3, to: 4, words: "1 rule", lines: ["Rule 1 is new."], more: 0 };
}

/** The real mapping of a queued pack Undo's answer (api-names and waitForAnswer), with the answer the bot gave. */
function undoPackThrough(queued: unknown, look: { state: string; error?: string; back?: unknown } | null) {
  const helpers = {
    call: async (method: string) => (method === "POST" ? queued : {}),
    restoreCall: async () => ({}),
    waitForAnswer: async () => {
      if (look === null) throw new Error("no answer");
      if (look.state === "failed") throw new ApiError(errorKinds[look.error ?? ""] ?? "server");
      return look;
    },
    root: "/api",
    url: async (path: string) => path,
  } as unknown as ApiHelpers;
  return namesArea(helpers).packUpdateUndoLast!;
}

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

  it("a pack DMbot took says so, with its changes and its Undo", async () => {
    render(<PackUpdatesCard api={packsApi({ waiting: [], taken: [moor] })} campaignRef={REF} />);
    expect(await screen.findByText("Moor Road pack was updated to version 4. DMbot took the new version.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "See changes" }));
    expect(screen.getByText("Changes:")).toBeTruthy();
    expect(screen.getByText("Rule 1 is new.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "↩️ Undo" })).toBeTruthy();
    expect(screen.getByText("Undo goes back to version 3. DMbot will take the next new version.")).toBeTruthy();
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

  it("a waiting pack has no Undo, only a taken one does", async () => {
    render(<PackUpdatesCard api={packsApi({ waiting: [lantern], taken: [moor] })} campaignRef={REF} />);
    await screen.findByText("Moor Road pack was updated to version 4. DMbot took the new version.");
    expect(screen.getAllByRole("button", { name: "↩️ Undo" })).toHaveLength(1);
  });

  it("Undo sends the pack and the version DMbot took, and says where it went back to", async () => {
    const undos: [string, string, number][] = [];
    const api = packsApi({ waiting: [], taken: [moor] });
    api.packUpdateUndoLast = async (ref, setId, version) => {
      undos.push([ref, setId, version]);
      return { back: 3 };
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 3. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
    expect(undos).toEqual([[REF, "s2", 4]]);
  });

  it("too late says so and reads the packs again", async () => {
    let reads = 0;
    const api = mockApi("table");
    api.packUpdates = async () => {
      reads += 1;
      return { waiting: [], taken: reads === 1 ? [moor] : [] };
    };
    api.packUpdateUndoLast = async () => {
      throw new ApiError("undo-late");
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Can't undo this one. It was already undone, or DMbot has since taken a newer version. Nothing changed.")).toBeTruthy();
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });

  it("the mock is asked for the Undo by name and arguments", async () => {
    const api = mockApi("table");
    render(<PackUpdatesCard api={api} campaignRef={`${REF}.pk-updates`} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 3. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
    expect(api.calls).toContain(`packUpdateUndoLast:${REF}.pk-updates:s2:4`);
  });

  it("a failed read keeps the packs and says so; the next read that works clears it", async () => {
    let reads = 0;
    const api = mockApi("table");
    api.packUpdates = async () => {
      reads += 1;
      if (reads === 2) throw new Error("offline");
      return reads === 1 ? { waiting: [], taken: [moor, moor2] } : { waiting: [], taken: [] };
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    await screen.findAllByRole("button", { name: "↩️ Undo" });
    fireEvent.click(screen.getAllByRole("button", { name: "↩️ Undo" })[0] as HTMLElement);
    expect(await screen.findByText("Couldn't load the changes.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "↩️ Undo" })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "↩️ Undo" })[1] as HTMLElement);
    await waitFor(() => expect(screen.queryByText("Couldn't load the changes.")).toBeNull());
  });

  it("a too-late Undo through the real mapping gives the too-late words", async () => {
    const api = packsApi({ waiting: [], taken: [moor] });
    api.packUpdateUndoLast = undoPackThrough({ answer: "a1" }, { state: "failed", error: "too_late" });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Can't undo this one. It was already undone, or DMbot has since taken a newer version. Nothing changed.")).toBeTruthy();
  });

  it("a queued Undo with no answer gives the plain words, through the real mapping", async () => {
    const api = packsApi({ waiting: [], taken: [moor] });
    api.packUpdateUndoLast = undoPackThrough({}, null);
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Couldn't do that just now. Nothing changed. Try again in a moment.")).toBeTruthy();
  });

  it("a back that is not a number falls back to the version the pack was on", async () => {
    const api = packsApi({ waiting: [], taken: [moor] });
    api.packUpdateUndoLast = undoPackThrough({ answer: "a1" }, { state: "done", back: "x" });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 3. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
  });

  it("any other failure gives the plain words", async () => {
    const api = packsApi({ waiting: [], taken: [moor] });
    api.packUpdateUndoLast = async () => {
      throw new Error("something else");
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Couldn't do that just now. Nothing changed. Try again in a moment.")).toBeTruthy();
  });

  it("more than four lines says so, Hide changes closes it, and the panel is named only while open", async () => {
    const many: PackUpdateItem = { ...lantern, more: 2 };
    render(<PackUpdatesCard api={packsApi({ waiting: [many], taken: [] })} campaignRef={REF} />);
    const see = (await screen.findByRole("button", { name: "See changes" })) as HTMLButtonElement;
    expect(see.getAttribute("aria-controls")).toBeNull();
    fireEvent.click(see);
    expect(screen.getByText("…and 2 more.")).toBeTruthy();
    const hide = screen.getByRole("button", { name: "Hide changes" }) as HTMLButtonElement;
    expect(hide.getAttribute("aria-controls")).toBeTruthy();
    fireEvent.click(hide);
    expect(screen.queryByText("…and 2 more.")).toBeNull();
    expect(screen.getByRole("button", { name: "See changes" }).getAttribute("aria-controls")).toBeNull();
  });

  it("nothing reaches the Shared rules page while RULE_UPDATES_LIVE is off", async () => {
    gate.on = false;
    const api = mockApi("table");
    render(<SharingPage api={api} />);
    await waitFor(() => expect(api.calls).toContain("sharedRules"));
    expect(api.calls.some((c) => c.startsWith("packUpdates:"))).toBe(false);
  });
});

describe("Pick changes on a pack (#4873)", () => {
  /** An API whose pack pick and Undo answer as given; it records the pick's arguments and the Undo's take. */
  function pickApi(over: {
    waiting?: PackUpdateItem[];
    pick?: (kept: number[]) => Promise<{ kept: number; left: number; skipped: number; take: number | null }>;
    undo?: () => Promise<void>;
  } = {}) {
    const picks: [string, string, number, number[]][] = [];
    const undos: number[] = [];
    const api = packsApi({ waiting: over.waiting ?? [lantern], taken: [] });
    api.packUpdatePick = async (ref, setId, version, kept) => {
      picks.push([ref, setId, version, kept]);
      return over.pick ? over.pick(kept) : { kept: kept.length, left: 2 - kept.length, skipped: 0, take: 7 };
    };
    api.packUpdateUndo = async (_ref, take) => {
      undos.push(take);
      if (over.undo) await over.undo();
    };
    return { api, picks, undos };
  }

  it("the picker opens with every change ticked, and Save is disabled until one is left out", async () => {
    const { api } = pickApi();
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    expect(
      await screen.findByText(
        "Pick the changes to keep. Leaving anything out ends your follow of Lantern Tavern pack: your table keeps its own copy of the entries, with the changes you kept, as its own homebrew, and stops getting this pack's updates. You can undo it for a day.",
      ),
    ).toBeTruthy();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true]);
    expect(((await screen.findByRole("button", { name: "Save my picks" })) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(boxes[1] as HTMLElement);
    expect(((await screen.findByRole("button", { name: "Save my picks" })) as HTMLButtonElement).disabled).toBe(false);
  });

  it("Save sends the pack, its version and the changes kept, and Never mind sends nothing", async () => {
    const { api, picks } = pickApi();
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    await waitFor(() => expect(picks).toEqual([[REF, "s1", 2, [0]]]));
    cleanup();
    const none = pickApi();
    render(<PackUpdatesCard api={none.api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Never mind" }));
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(none.picks).toEqual([]);
  });

  it("the done words with their counts, then Undo sends the take and says it is back on the version it followed", async () => {
    const { api, undos } = pickApi();
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("✅ Done. Lantern Tavern pack is now your own homebrew: you kept 1 change and left out 1 change. It won't update any more.")).toBeTruthy();
    expect(screen.getByText("You can undo this for a day.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 1 and following it again. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
    expect(undos).toEqual([7]);
  });

  it("the skipped sentence shows only when some entries were already the campaign's own", async () => {
    const { api } = pickApi({ pick: async () => ({ kept: 1, left: 1, skipped: 2, take: 7 }) });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("2 entries were already yours, so they stayed as they were.")).toBeTruthy();
  });

  it("each refusal of a pick says its plain words, and a changed pack closes the picker", async () => {
    const cases: [string, string][] = [
      ["changed", "The pack changed again while you were looking. Look at the new changes first."],
      ["gone", "This campaign isn't following that pack, or there is nothing new to take."],
      ["no-room", "Your own homebrew and this pack together are more than the limit. Remove some first."],
      ["too-many-masters", "This update has more changes than can be listed here. Use Pick changes in Discord."],
      ["server", "Couldn't do that just now. Nothing changed. Try again in a moment."],
    ];
    for (const [kind, line] of cases) {
      cleanup();
      const { api } = pickApi({
        pick: async () => {
          throw new ApiError(kind as "gone");
        },
      });
      render(<PackUpdatesCard api={api} campaignRef={REF} />);
      fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
      fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
      fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
      expect(await screen.findByText(line)).toBeTruthy();
      if (kind === "changed") expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    }
  });

  it("a late Undo of a pick says so and changes nothing", async () => {
    const { api } = pickApi({
      undo: async () => {
        throw new ApiError("undo-late");
      },
    });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Too late to undo this one. Nothing changed.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });

  it("the picker is not offered past the list (more than 50 changes)", async () => {
    const many: PackUpdateItem = { ...lantern, more: 2 };
    const { api } = pickApi({ waiting: [many] });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    await screen.findByRole("button", { name: "See changes" });
    expect(screen.queryByRole("button", { name: "Pick changes" })).toBeNull();
  });

  it("a re-read with changed lines closes the picker, so no old tick is sent with a newer version", async () => {
    let reads = 0;
    const api = mockApi("table");
    api.packUpdates = async () => {
      reads += 1;
      const changed = { ...lantern, lines: ["Rule 1 now says a torch lasts an hour.", "A new rule on rope.", "A third rule."] };
      return { waiting: [reads === 1 ? lantern : changed], taken: [moor] };
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    expect(await screen.findAllByRole("checkbox")).toHaveLength(2);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    await waitFor(() => expect(screen.queryAllByRole("checkbox")).toHaveLength(0));
  });

  it("a pick that is still waiting says so, and the alert clears when the Pick button is pressed again", async () => {
    const { api } = pickApi({
      pick: async () => {
        throw new ApiError("still-waiting");
      },
    });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("DMbot is taking a while. Look again in a moment before trying again.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pick changes" }));
    expect(screen.queryByText("DMbot is taking a while. Look again in a moment before trying again.")).toBeNull();
  });

  it("the picker is a group of changes with a legend, takes focus on open, and the Pick button names it", async () => {
    const { api } = pickApi();
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    const pick = (await screen.findByRole("button", { name: "Pick changes" })) as HTMLButtonElement;
    fireEvent.click(pick);
    expect(await screen.findByRole("group", { name: "Changes to keep" })).toBeTruthy();
    expect(document.activeElement).toBe((await screen.findAllByRole("checkbox"))[0]);
    const controls = pick.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    expect(document.getElementById(controls as string)).toBeTruthy();
  });

  it("after a pick, focus goes to the saved line", async () => {
    const { api } = pickApi();
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    const done = await screen.findByText("✅ Done. Lantern Tavern pack is now your own homebrew: you kept 1 change and left out 1 change. It won't update any more.");
    expect(document.activeElement).toBe(done);
  });

  it("a pick with no Undo handle shows the done line without an Undo", async () => {
    const { api } = pickApi({ pick: async () => ({ kept: 1, left: 1, skipped: 0, take: null }) });
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText(/✅ Done\. Lantern Tavern pack/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });

  it("the picks and Undo go through the real error map: its words, its counts, and the Undo handle", async () => {
    const through = (queued: unknown, look: Record<string, unknown> | null) => {
      const helpers = {
        call: async (method: string) => (method === "POST" ? queued : {}),
        restoreCall: async () => ({}),
        waitForAnswer: async (_path: string, onLook?: (got: unknown) => void) => {
          if (look === null) throw new Error("no answer");
          onLook?.(look);
          if (look.state === "failed") throw new ApiError(errorKinds[(look.error as string) ?? ""] ?? "server");
          return look;
        },
        root: "/api",
        url: async (path: string) => path,
      } as unknown as ApiHelpers;
      return namesArea(helpers);
    };
    const refuse = async (error: string, kind: string) => {
      await expect(through({ answer: "a1" }, { state: "failed", error })!.packUpdatePick!(REF, "s1", 2, [0])).rejects.toMatchObject({ kind });
    };
    await refuse("no_room", "no-room");
    await refuse("too_many", "too-many-masters");
    await refuse("changed", "changed");
    await refuse("gone", "gone");
    const counted = through({ answer: "a1" }, { state: "done", kept: 1, left: 1, skipped: 2, take: 7 });
    await expect(counted.packUpdatePick!(REF, "s1", 2, [0])).resolves.toEqual({ kept: 1, left: 1, skipped: 2, take: 7 });
    const noLeft = through({ answer: "a1" }, { state: "done", kept: 1 });
    await expect(noLeft.packUpdatePick!(REF, "s1", 2, [0])).rejects.toMatchObject({ kind: "server" });
    const late = through({ answer: "a1" }, { state: "failed", error: "too_late" });
    await expect(late.packUpdateUndo!(REF, 7)).rejects.toMatchObject({ kind: "undo-late" });
  });

  it("a second press of Undo while one is in flight is ignored", async () => {
    let release: () => void = () => undefined;
    const { api, undos } = pickApi({});
    api.packUpdateUndo = async (_ref, take) => {
      undos.push(take);
      await new Promise<void>((r) => (release = r));
    };
    render(<PackUpdatesCard api={api} campaignRef={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    const undo = await screen.findByRole("button", { name: "↩️ Undo" });
    fireEvent.click(undo);
    fireEvent.click(undo);
    expect(undos).toEqual([7]);
    release();
  });
});


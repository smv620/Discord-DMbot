/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "See changes" under a followed house-rules set (#4841 C1, #4845): what a newer version would change, shown as core gives it,
// and nothing taken. The card is tested on its own; the House rules page only mounts it while RULE_UPDATES_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import namesArea from "../src/account/api-names";
import { ApiError, type AccountApi, type ApiHelpers, errorKinds, type RuleUpdate } from "../src/account/api";
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

describe("Pick changes and its Undo (#4855)", () => {
  // A newer version with nothing more to list: the picker is offered.
  const small: RuleUpdate = { ...waiting, more: 0 };

  /** An API whose pick and undo answer as given, and whose read comes back empty once a pick has ended the follow. */
  function pickingApi(over: {
    first?: RuleUpdate;
    pick?: (ref: string, version: number, kept: number[]) => Promise<{ kept: number; left: number; take: number | null }>;
    undo?: (ref: string, take: number) => Promise<void>;
  } = {}) {
    const picks: { ref: string; version: number; kept: number[] }[] = [];
    const undos: { ref: string; take: number }[] = [];
    let reads = 0;
    const api = mockApi("table");
    api.ruleUpdate = async () => {
      reads += 1;
      return { update: reads > 1 ? null : (over.first ?? small) };
    };
    api.ruleUpdatePick = async (ref, version, kept) => {
      picks.push({ ref, version, kept });
      return over.pick ? over.pick(ref, version, kept) : { kept: kept.length, left: 3 - kept.length, take: 7 };
    };
    api.ruleUpdateUndo = async (ref, take) => {
      undos.push({ ref, take });
      if (over.undo) await over.undo(ref, take);
    };
    return { api, picks, undos, reads: () => reads };
  }

  it("offers Pick changes when nothing is left to list, and says so instead when there is more", async () => {
    const { api } = pickingApi({ first: small });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect(await screen.findByRole("button", { name: "Pick changes" })).toBeTruthy();
    cleanup();
    const more = pickingApi({ first: waiting });
    render(<RuleUpdateCard api={more.api} campaignRef={REF} name="Tavern rules" />);
    expect(await screen.findByText("This update has more changes than can be listed here. Use Pick changes in Discord.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pick changes" })).toBeNull();
  });

  it("opens the picker with its words and one box per change, all ticked", async () => {
    const { api } = pickingApi();
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    expect(
      await screen.findByText(
        "Pick the changes to keep. Leaving anything out ends your follow of Tavern rules: your table keeps its own copy of the rules, with the changes you kept, and stops getting this set's updates. You can undo it for a day.",
      ),
    ).toBeTruthy();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true, true]);
    expect(boxes.map((b) => b.closest("label")?.textContent)).toEqual(small.lines);
  });

  it("keeps Save my picks disabled until one change is left out", async () => {
    const { api } = pickingApi();
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    const save = (await screen.findByRole("button", { name: "Save my picks" })) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getAllByRole("checkbox")[1] as HTMLElement);
    expect(((await screen.findByRole("button", { name: "Save my picks" })) as HTMLButtonElement).disabled).toBe(false);
  });

  it("sends the version and the indexes kept, then shows Done with its counts and the Undo, and keeps them after the read comes back empty", async () => {
    const { api, picks, reads } = pickingApi();
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("✅ Done. Tavern rules is now your own copy: you kept 2 changes and left out 1 change. You no longer get its updates. Your own rules still win.")).toBeTruthy();
    expect(picks).toEqual([{ ref: REF, version: 3, kept: [0, 2] }]);
    expect(screen.getByText("You can undo this for a day.")).toBeTruthy();
    await waitFor(() => expect(reads()).toBeGreaterThan(1));
    expect(screen.getByText("✅ Done. Tavern rules is now your own copy: you kept 2 changes and left out 1 change. You no longer get its updates. Your own rules still win.")).toBeTruthy();
  });

  it("Undo sends the take and says it is undone, back on the version it followed", async () => {
    const { api, undos } = pickingApi();
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 2 and following it again. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
    expect(undos).toEqual([{ ref: REF, take: 7 }]);
  });

  it("Never mind closes the picker and sends nothing", async () => {
    const { api, picks } = pickingApi();
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Never mind" }));
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(picks).toEqual([]);
  });

  it("says each refusal in plain words, and nothing is changed by a failed pick", async () => {
    const cases: [ApiError | Error, string][] = [
      [new ApiError("changed"), "The set changed again while you were looking. Look at the new changes first."],
      [new ApiError("gone"), "This campaign isn't following a set of house rules, or there is nothing new to take."],
      [new ApiError("no-room"), "Your own house rules and this set together are more than the limit. Remove some first."],
      [new ApiError("server"), "Couldn't do that just now. Nothing changed. Try again in a moment."],
    ];
    for (const [error, line] of cases) {
      cleanup();
      const { api } = pickingApi({
        pick: async () => {
          throw error;
        },
      });
      render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
      fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
      fireEvent.click((await screen.findAllByRole("checkbox"))[2] as HTMLElement);
      fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
      expect((await screen.findByRole("alert")).textContent).toBe(line);
    }
  });

  it("a changed set closes the picker and reads the update again", async () => {
    const { api, reads } = pickingApi({
      pick: async () => {
        throw new ApiError("changed");
      },
    });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[2] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    await screen.findByRole("alert");
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    await waitFor(() => expect(reads()).toBeGreaterThan(1));
  });

  it("says a late Undo is too late, and changes nothing", async () => {
    const { api } = pickingApi({
      undo: async () => {
        throw new ApiError("undo-late");
      },
    });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too late to undo this one. Nothing changed.");
    expect(screen.getByText("✅ Done. Tavern rules is now your own copy: you kept 2 changes and left out 1 change. You no longer get its updates. Your own rules still win.")).toBeTruthy();
  });

  it("the mock is asked for the pick and the undo by name and arguments", async () => {
    const api = mockApi("table");
    render(<RuleUpdateCard api={api} campaignRef={`${REF}.ru-small`} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("✅ Done. Tavern rules is now your own copy: you kept 2 changes and left out 1 change. You no longer get its updates. Your own rules still win.")).toBeTruthy();
    expect(api.calls).toContain(`ruleUpdatePick:${REF}.ru-small:3:1,2`);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    await screen.findByText(/↩️ Undone\./);
    expect(api.calls.some((c) => c.startsWith(`ruleUpdateUndo:${REF}.ru-small:`))).toBe(true);
  });
});

describe("Undo for an update DMbot already took (#4867)", () => {
  const taken: RuleUpdate = { ...waiting, more: 0 };

  /** An API that answers the taken read as given, and records the undo's version. */
  function takenApi(answer: { taken: RuleUpdate | null } | "fail", update: RuleUpdate | null = null, undo?: () => Promise<{ back: number | null }>) {
    const undos: number[] = [];
    let reads = 0;
    const api = mockApi("table");
    api.ruleUpdate = async () => ({ update });
    api.ruleTaken = async () => {
      reads += 1;
      if (answer === "fail") throw new Error("offline");
      return answer;
    };
    api.ruleUpdateUndoLast = async (_ref, version) => {
      undos.push(version);
      return undo ? undo() : { back: 2 };
    };
    return { api, undos, reads: () => reads };
  }

  /** The real mapping of a queued Undo's answer (api-names and waitForAnswer), with the answer the bot gave. */
  function undoLastThrough(queued: unknown, look: { state: string; error?: string } | null) {
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
    return namesArea(helpers).ruleUpdateUndoLast!;
  }

  it("shows the update DMbot took, with its line; nothing when none was taken", async () => {
    const { api } = takenApi({ taken });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect(await screen.findByText("Tavern rules house rules were updated to version 3. DMbot took the new version.")).toBeTruthy();
    expect(screen.getByText("Undo goes back to version 2. DMbot will take the next new version.")).toBeTruthy();
    cleanup();
    const none = takenApi({ taken: null });
    const { container } = render(<RuleUpdateCard api={none.api} campaignRef={REF} name="Tavern rules" />);
    await waitFor(() => expect(none.reads()).toBeGreaterThan(0));
    expect(container.textContent).toBe("");
  });

  it("See changes opens the lines, and the more line when more is above zero", async () => {
    const { api } = takenApi({ taken: { ...taken, more: 2 } });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "See changes" }));
    expect(screen.getByText("Changes:")).toBeTruthy();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Rule 2 now says a short rest is one hour.",
      "A new rule on torches.",
      "Rule 5 is gone.",
    ]);
    expect(screen.getByText("…and 2 more.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide changes" }));
    expect(screen.queryByText("Changes:")).toBeNull();
  });

  it("Undo sends the version DMbot took, and says it is back on the version it went back to", async () => {
    const { api, undos } = takenApi({ taken });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("↩️ Undone. Back on version 2 and following it again. DMbot will take the next new version; use Pick changes to choose.")).toBeTruthy();
    expect(undos).toEqual([3]);
  });

  it("too late, through the real mapping, says so and reads the update again until the Undo is gone", async () => {
    let reads = 0;
    const api = mockApi("table");
    api.ruleUpdate = async () => ({ update: null });
    api.ruleTaken = async () => {
      reads += 1;
      return { taken: reads === 1 ? taken : null };
    };
    api.ruleUpdateUndoLast = undoLastThrough({ answer: "a1" }, { state: "failed", error: "too_late" });
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Can't undo this one. It was already undone, or DMbot has since taken a newer version. Nothing changed.")).toBeTruthy();
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(screen.queryByRole("button", { name: "↩️ Undo" })).toBeNull();
  });

  it("a queued Undo with no answer shows the plain words, through the real mapping", async () => {
    const api = mockApi("table");
    api.ruleUpdate = async () => ({ update: null });
    api.ruleTaken = async () => ({ taken });
    api.ruleUpdateUndoLast = undoLastThrough({}, null);
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "↩️ Undo" }));
    expect(await screen.findByText("Couldn't do that just now. Nothing changed. Try again in a moment.")).toBeTruthy();
  });

  it("a waiting update hides the taken part", async () => {
    const { api } = takenApi({ taken }, waiting);
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect(await screen.findByText("A newer version of Tavern rules is waiting: version 3 (you follow version 2).")).toBeTruthy();
    expect(screen.queryByText("Tavern rules house rules were updated to version 3. DMbot took the new version.")).toBeNull();
  });

  it("a failed read of the taken update shows the failed words", async () => {
    const { api } = takenApi("fail");
    render(<RuleUpdateCard api={api} campaignRef={REF} name="Tavern rules" />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the changes.");
  });
});

describe("the sets' picker: a labelled group and focus (#4884)", () => {
  const small: RuleUpdate = { ...waiting, more: 0 };

  /** An API that answers the pick as given, or refuses it. */
  function pickApi(pick: () => Promise<{ kept: number; left: number; take: number | null }>) {
    const api = mockApi("table");
    let reads = 0;
    api.ruleUpdate = async () => {
      reads += 1;
      return { update: reads > 1 ? null : small };
    };
    api.ruleUpdatePick = pick;
    return api;
  }

  it("the checkboxes sit in a group named by the intro's first sentence, and focus is in the picker on open", async () => {
    render(<RuleUpdateCard api={pickApi(async () => ({ kept: 1, left: 2, take: 7 }))} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    expect(await screen.findByRole("group", { name: "Pick the changes to keep." })).toBeTruthy();
    expect(document.activeElement).toBe((await screen.findAllByRole("checkbox"))[0]);
  });

  it("the Pick button names its picker only while it is open", async () => {
    render(<RuleUpdateCard api={pickApi(async () => ({ kept: 1, left: 2, take: 7 }))} campaignRef={REF} name="Tavern rules" />);
    const pick = (await screen.findByRole("button", { name: "Pick changes" })) as HTMLButtonElement;
    expect(pick.getAttribute("aria-controls")).toBeNull();
    fireEvent.click(pick);
    await waitFor(() => expect(pick.getAttribute("aria-controls")).toBeTruthy());
    const controls = pick.getAttribute("aria-controls") as string;
    await waitFor(() => expect(document.getElementById(controls)).toBeTruthy());
  });

  it("Never mind returns focus to the Pick changes button", async () => {
    render(<RuleUpdateCard api={pickApi(async () => ({ kept: 1, left: 2, take: 7 }))} campaignRef={REF} name="Tavern rules" />);
    const pick = (await screen.findByRole("button", { name: "Pick changes" })) as HTMLButtonElement;
    fireEvent.click(pick);
    fireEvent.click(await screen.findByRole("button", { name: "Never mind" }));
    expect(document.activeElement).toBe(pick);
  });

  it("after a refused pick, focus goes back into the picker", async () => {
    render(
      <RuleUpdateCard
        api={pickApi(async () => {
          throw new ApiError("gone");
        })}
        campaignRef={REF}
        name="Tavern rules"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    const boxes = await screen.findAllByRole("checkbox");
    fireEvent.click(boxes[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    expect(await screen.findByText("This campaign isn't following a set of house rules, or there is nothing new to take.")).toBeTruthy();
    expect(document.activeElement).toBe((await screen.findAllByRole("checkbox"))[0]);
  });

  it("after a saved pick, focus goes to the saved line", async () => {
    render(<RuleUpdateCard api={pickApi(async () => ({ kept: 1, left: 1, take: 7 }))} campaignRef={REF} name="Tavern rules" />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick changes" }));
    fireEvent.click((await screen.findAllByRole("checkbox"))[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save my picks" }));
    const done = await screen.findByText("✅ Done. Tavern rules is now your own copy: you kept 1 change and left out 1 change. You no longer get its updates. Your own rules still win.");
    await waitFor(() => expect(document.activeElement).toBe(done));
  });
});


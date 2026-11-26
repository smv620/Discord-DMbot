/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Let me choose" on the join question (#4841, the page half of #4891): the DM picks a session or a story thread to catch the new
// character up from, each pick with its own Undo. The card is tested on its own; the join card mounts it while PARTY_JOIN_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import namesArea from "../src/account/api-names";
import { ApiError, type AccountApi, type ApiHelpers, errorKinds, type JoinChoices } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import JoinChoose from "../src/account/JoinChoose";

afterEach(() => {
  cleanup();
});

const REF = "100.cmp1";
const GROUP = "g1";
const mira: JoinChoices = {
  madeAt: 1_800_000_000,
  choices: {
    members: [{ id: "n1234567", name: "Mira" }],
    sessions: [{ at: 1000, number: 3, counts: { n1234567: 14 } }],
    threads: [{ id: "t1", name: "The Ashen Road", counts: { n1234567: 9 } }],
    sessionsInAll: 1,
    threadsInAll: 1,
    nobody: false,
  },
};

/** An API that answers the choices as given, records the asks and the picks, and runs the Undo as given. */
function choiceApi(over: {
  choices?: JoinChoices;
  waitingAgain?: boolean;
  ask?: () => Promise<void>;
  choose?: (pick: unknown) => Promise<{ chose: { id: string; count: number }[]; choseMore: number; first: boolean; batch: number | null }>;
  undo?: () => Promise<{ batch: number | null; given: number | null }>;
}) {
  const asks: string[] = [];
  const picks: unknown[] = [];
  const undos: unknown[] = [];
  const api = mockApi("table");
  api.askJoinChoices = async () => {
    asks.push(GROUP);
    if (over.ask) await over.ask();
  };
  api.joinChoices = async () => over.choices ?? mira;
  api.joinQuestions = async () => (over.waitingAgain ? [{ group: GROUP, answer: "waiting", names: ["Mira"] }] : []);
  api.joinChoose = async (_ref, _group, pick) => {
    picks.push(pick);
    return over.choose ? over.choose(pick) : { chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: true, batch: 9 };
  };
  api.answerJoin = async (_ref, change) => {
    undos.push(change);
    if (over.undo) return over.undo();
    return { batch: null, given: null };
  };
  return { api, asks, picks, undos };
}

/** Clicks a button once it is enabled: the Undo appears while the pick is still settling (#4891). */
async function clickWhenEnabled(name: string): Promise<void> {
  const button = (await screen.findByRole("button", { name })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
}

/** Clicks the first Give button (the first session's), once it is enabled. */
async function clickFirstGive(): Promise<void> {
  const button = (await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
}

/** Clicks the first Undo button (the first pick's), once it is enabled. */
async function clickFirstUndo(): Promise<void> {
  const button = (await screen.findAllByRole("button", { name: "↩️ Undo" }))[0] as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
}

function card(api: AccountApi, names = ["Mira"], back = false) {
  return <JoinChoose api={api} campaignRef={REF} group={GROUP} names={names} back={back} onClose={() => undefined} />;
}

describe("Let me choose on the join question (#4891)", () => {
  it("asks the bot on opening, then shows the head, the lists, and the notes", async () => {
    const { api, asks } = choiceApi({});
    render(card(api));
    expect(await screen.findByText("Choose what Mira knows. Nothing changes until you pick.")).toBeTruthy();
    expect(asks).toEqual([GROUP]);
    expect(screen.getByText("Until then Mira knows only what everyone knows.")).toBeTruthy();
    expect(screen.getByText("Each pick gives Mira what the rest of the party knows from that session or story thread, with its own ↩️ Undo.")).toBeTruthy();
    expect(screen.getByText("Session 3")).toBeTruthy();
    expect(screen.getByText("14 things Mira doesn't know yet")).toBeTruthy();
    expect(screen.getByText("The Ashen Road")).toBeTruthy();
    expect(screen.getByText("9 things Mira doesn't know yet")).toBeTruthy();
    expect(screen.getByText("Uploaded lore and notes you typed in aren't part of any session. Find those by story thread, or use 👁️ Who knows this? on any card.")).toBeTruthy();
  });

  it("while the bot works, the card shows what it is doing and the seconds so far; a failed ask says so and Look again asks again", async () => {
    let fail = true;
    const { api, asks } = choiceApi({
      ask: async () => {
        if (fail) throw new ApiError("server");
      },
    });
    render(card(api));
    expect(await screen.findByText("Couldn't work it out just now. Nothing changed. Try again in a moment.")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Look again" }));
    expect(await screen.findByText("Choose what Mira knows. Nothing changes until you pick.")).toBeTruthy();
    expect(asks).toHaveLength(2);
  });

  it("a session pick sends its time and says what it gave; Undo sends its batch and says it is undone", async () => {
    const { api, picks, undos } = choiceApi({});
    render(card(api));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    expect(await screen.findByText("✅ Mira now knows 14 things the rest of the party knew from session 3. Nothing was sent to Mira's player.")).toBeTruthy();
    expect(picks).toEqual([{ session: 1000 }]);
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("↩️ Undone. Mira no longer knows what that pick gave.")).toBeTruthy();
    expect(undos).toEqual([{ action: "undo", batch: 9 }]);
  });

  it("a story thread pick sends its id", async () => {
    const { api, picks } = choiceApi({});
    render(card(api));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[1] as HTMLElement);
    await waitFor(() => expect(picks).toEqual([{ thread: "t1" }]));
    expect(await screen.findByText("✅ Mira now knows 14 things the rest of the party knew from the story thread The Ashen Road. Nothing was sent to Mira's player.")).toBeTruthy();
  });

  it("several characters: the done line names each count, and more characters given things is one extra sentence", async () => {
    const two: JoinChoices = {
      madeAt: 1_800_000_000,
      choices: {
        members: [
          { id: "n1234567", name: "Mira" },
          { id: "n7654321", name: "Tavin" },
        ],
        sessions: [{ at: 1000, number: 3, counts: { n1234567: 14, n7654321: 9 } }],
        threads: [],
        sessionsInAll: 1,
        threadsInAll: 0,
        nobody: false,
      },
    };
    const { api } = choiceApi({
      choices: two,
      choose: async () => ({ chose: [{ id: "n1234567", count: 14 }, { id: "n7654321", count: 9 }], choseMore: 1, first: true, batch: 9 }),
    });
    render(card(api, ["Mira", "Tavin"]));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    expect(await screen.findByText("✅ From session 3, Mira now knows 14 more things and Tavin 9. Nothing was sent to the players. 1 more character was given things too.")).toBeTruthy();
  });

  it("a later pick's Undo says what that pick gave is no longer known", async () => {
    const { api } = choiceApi({ choose: async () => ({ chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: false, batch: 9 }) });
    render(card(api));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("↩️ Undone. Mira no longer knows what that pick gave.")).toBeTruthy();
  });

  it("a pick that was already answered, and a late Undo, say so and change nothing", async () => {
    const gone = choiceApi({
      choose: async () => {
        throw new ApiError("gone");
      },
    });
    render(card(gone.api));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    expect(await screen.findByText("Already answered (by you or another DM). Nothing changed.")).toBeTruthy();
    cleanup();
    const late = choiceApi({
      undo: async () => {
        throw new ApiError("undo-late");
      },
    });
    render(card(late.api));
    fireEvent.click((await screen.findAllByRole("button", { name: "Give" }))[0] as HTMLElement);
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("Can't undo this one: some of it changed since. Open the character's card to fix it.")).toBeTruthy();
  });

  it("nothing to give says so, and the lists say when they are cut", async () => {
    const none: JoinChoices = { madeAt: 1_800_000_000, choices: { ...mira.choices!, sessions: [], threads: [] } };
    const { api } = choiceApi({ choices: none });
    render(card(api));
    expect(await screen.findByText("There's nothing left to choose: Mira knows everything the rest of the party knows from the sessions and story threads. To give something else, use 👁️ Who knows this? on it.")).toBeTruthy();
    cleanup();
    const cut: JoinChoices = { madeAt: 1_800_000_000, choices: { ...mira.choices!, sessionsInAll: 30 } };
    const second = choiceApi({ choices: cut });
    render(card(second.api));
    expect(await screen.findByText("Showing the last 25 sessions.")).toBeTruthy();
  });

  it("Close calls back", async () => {
    const { api } = choiceApi({});
    const onClose = vi.fn();
    render(<JoinChoose api={api} campaignRef={REF} group={GROUP} names={["Mira"]} back={false} onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the answers of a pick go through the real error map: gone and nothing left", async () => {
    const through = (queued: unknown, look: Record<string, unknown> | null) => {
      const helpers = {
        call: async (method: string) => (method === "POST" ? queued : {}),
        restoreCall: async () => ({}),
        waitForAnswer: async () => {
          if (look === null) throw new Error("no answer");
          if (look.state === "failed") throw new ApiError(errorKinds[(look.error as string) ?? ""] ?? "server");
          return look;
        },
        root: "/api",
        url: async (path: string) => path,
      } as unknown as ApiHelpers;
      return namesArea(helpers).joinChoose!;
    };
    await expect(through({ answer: "a1" }, { state: "failed", error: "gone" })(REF, GROUP, { session: 1000 })).rejects.toMatchObject({ kind: "gone" });
    await expect(through({ answer: "a1" }, { state: "failed", error: "nothing_left" })(REF, GROUP, { thread: "t1" })).rejects.toMatchObject({ kind: "nothing-left" });
    await expect(through({ answer: "a1" }, { state: "done", chose: [{ id: "n1234567", count: 14 }], choseMore: 2, first: true, batch: 9 })(REF, GROUP, { session: 1000 })).resolves.toEqual({
      chose: [{ id: "n1234567", count: 14 }],
      choseMore: 2,
      first: true,
      batch: 9,
    });
  });

  it("each pick keeps its own line and Undo: undoing the first changes only its line", async () => {
    const { api, undos } = choiceApi({
      waitingAgain: true,
      choose: async () => ({ chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: true, batch: 9 }),
    });
    render(card(api));
    await clickFirstGive();
    await screen.findByText("✅ Mira now knows 14 things the rest of the party knew from session 3. Nothing was sent to Mira's player.");
    await waitFor(() => expect(screen.getAllByRole("button", { name: "↩️ Undo" })).toHaveLength(1));
    await clickFirstGive();
    await waitFor(() => expect(screen.getAllByRole("button", { name: "↩️ Undo" })).toHaveLength(2));
    await clickFirstUndo();
    expect(await screen.findByText("↩️ Undone. Mira knows only what everyone knows again, plus what was said at the table since.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "↩️ Undo" })).toHaveLength(1);
    expect(screen.getAllByText("✅ Mira now knows 14 things the rest of the party knew from session 3. Nothing was sent to Mira's player.")).toHaveLength(1);
    expect(undos).toEqual([{ action: "undo", batch: 9 }]);
  });

  it("nothing left to give from a pick says so on the card, in the join words", async () => {
    const { api } = choiceApi({
      choose: async () => {
        throw new ApiError("nothing-left");
      },
    });
    render(card(api));
    await clickFirstGive();
    expect(await screen.findByText("Mira already knows everything the rest of the party knows from session 3 (another DM may have given it). Nothing changed.")).toBeTruthy();
  });

  it("a too-late pick says the join card's too-late line, and a gone Undo says it too", async () => {
    const pickLate = choiceApi({
      choose: async () => {
        throw new ApiError("undo-late");
      },
    });
    render(card(pickLate.api));
    await clickFirstGive();
    expect(await screen.findByText("Can't undo this one: some of it changed since. Open the character's card to fix it.")).toBeTruthy();
    cleanup();
    const goneUndo = choiceApi({
      undo: async () => {
        throw new ApiError("gone");
      },
    });
    render(card(goneUndo.api));
    await clickFirstGive();
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("Can't undo this one: some of it changed since. Open the character's card to fix it.")).toBeTruthy();
  });

  it("a count of zero is left out of the line", async () => {
    const zero: JoinChoices = {
      madeAt: 1_800_000_000,
      choices: {
        members: [
          { id: "n1234567", name: "Mira" },
          { id: "n7654321", name: "Tavin" },
        ],
        sessions: [{ at: 1000, number: 3, counts: { n1234567: 14, n7654321: 0 } }],
        threads: [],
        sessionsInAll: 1,
        threadsInAll: 0,
        nobody: false,
      },
    };
    const { api } = choiceApi({ choices: zero });
    render(card(api, ["Mira", "Tavin"]));
    expect(await screen.findByText("14 things Mira doesn't know yet")).toBeTruthy();
    expect(screen.queryByText(/Tavin doesn't know/)).toBeNull();
    expect(screen.queryByText(/Tavin 0/)).toBeNull();
  });

  it("a pick whose id matches no character says the generic done line, with its Undo", async () => {
    const { api } = choiceApi({ choose: async () => ({ chose: [{ id: "zzzzzzzz", count: 4 }], choseMore: 0, first: false, batch: 11 }) });
    render(card(api));
    await clickFirstGive();
    expect(await screen.findByText("✅ Done. Nothing was sent to the players.")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "↩️ Undo" })).toBeTruthy();
  });

  it("a failed re-read keeps the list and says the counts may be out of date", async () => {
    const { api } = choiceApi({});
    render(card(api));
    await screen.findByText("Choose what Mira knows. Nothing changes until you pick.");
    api.joinChoices = async () => {
      throw new Error("offline");
    };
    await clickFirstGive();
    expect(await screen.findByText("These counts may be out of date. Press Look again.")).toBeTruthy();
    expect(screen.getByText("Session 3")).toBeTruthy();
  });

  it("a back question whose first pick is undone says what was known before leaving, when it waits again", async () => {
    const { api } = choiceApi({ waitingAgain: true, choose: async () => ({ chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: true, batch: 9 }) });
    render(card(api, ["Mira"], true));
    expect(await screen.findByText("Until then Mira knows only what was known before leaving.")).toBeTruthy();
    await clickFirstGive();
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("↩️ Undone. Mira knows only what Mira knew before leaving, plus what was said at the table since.")).toBeTruthy();
  });

  it("a back question whose question was answered otherwise says the later-pick line", async () => {
    const { api } = choiceApi({ waitingAgain: false, choose: async () => ({ chose: [{ id: "n1234567", count: 14 }], choseMore: 0, first: true, batch: 9 }) });
    render(card(api, ["Mira"], true));
    await screen.findByText("Until then Mira knows only what was known before leaving.");
    await clickFirstGive();
    await clickWhenEnabled("↩️ Undo");
    expect(await screen.findByText("↩️ Undone. Mira no longer knows what that pick gave.")).toBeTruthy();
  });
});


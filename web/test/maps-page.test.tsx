/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Maps page (#4347): the DM's view and presses with Undo, the confirmations for show and remove, and the player's view.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Maps, { refFromPath } from "../src/account/Maps";
import { mapsWords as w } from "../src/content/maps-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/maps-switch", () => ({
  get MAPS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "100.cmp1";
const PLAYER = "100.cmp1.player";
const rowFor = (title: string): HTMLElement => screen.getByText(title).closest("li") as HTMLElement;

describe("the Maps page", () => {
  it("reads the campaign from the address", () => {
    expect(refFromPath("/account/maps/100.cmp1")).toBe("100.cmp1");
    expect(refFromPath("/account/maps/")).toBeNull();
  });

  it("says it is not open yet while the switch is off, and asks for nothing", () => {
    live.on = false;
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    expect(screen.getByText(w.notOpen)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("maps:"))).toBe(false);
  });

  it("shows a DM every map, with the question on a waiting one", async () => {
    render(<Maps api={mockApi("table")} refId={DM} />);
    await screen.findByText("Icewind Dale");
    // (the campaign's navigation has lists of its own, so the items inside a navigation are not counted)
    expect(screen.getAllByRole("listitem").filter((li) => !li.closest("nav")).length).toBe(5);
    expect(within(rowFor("Cave")).getByText(w.dmTitle("Dee"))).toBeTruthy();
    expect(within(rowFor("Cave")).getByRole("button", { name: w.useYes })).toBeTruthy();
  });

  it("answers yes, says so, and Undo puts the question back", async () => {
    render(<Maps api={mockApi("table")} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.doneYes);
    expect(within(rowFor("Cave")).queryByRole("button", { name: w.useYes })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    await screen.findByText(w.undone);
    await waitFor(() => expect(within(rowFor("Cave")).getByRole("button", { name: w.useYes })).toBeTruthy());
  });

  it("asks before showing to the table and before removing, and Cancel changes nothing", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("The Keep, floor 1");
    fireEvent.click(within(rowFor("The Keep, floor 1")).getByRole("button", { name: w.show }));
    expect(screen.getByText(w.showWarning)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.cancel }));
    expect(api.calls.some((c) => c.startsWith("changeMaps:"))).toBe(false);
    fireEvent.click(within(rowFor("The Keep, floor 1")).getByRole("button", { name: w.show }));
    fireEvent.click(screen.getByRole("button", { name: w.showIt }));
    await screen.findByText(w.shownDone);
    fireEvent.click(within(rowFor("Icewind Dale")).getByRole("button", { name: w.removeIt }));
    expect(screen.getByText(w.removeAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    expect(screen.getByText("Icewind Dale")).toBeTruthy();
  });

  it("removes a map after the question, and Undo brings it back", async () => {
    render(<Maps api={mockApi("table")} refId={DM} />);
    await screen.findByText("Old road");
    fireEvent.click(within(rowFor("Old road")).getByRole("button", { name: w.removeIt }));
    fireEvent.click(within(rowFor("Old road")).getByRole("button", { name: w.removeYes }));
    await screen.findByText(w.doneRemoved);
    expect(screen.queryByText("Old road")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    await screen.findByText("Old road");
  });

  it("shows a player only the shown maps and their own, with no DM buttons", async () => {
    render(<Maps api={mockApi("table")} refId={PLAYER} />);
    await screen.findByText("Icewind Dale");
    expect(screen.queryByText("The Keep, floor 1")).toBeNull();
    expect(screen.queryByRole("button", { name: w.useYes })).toBeNull();
    expect(screen.queryByRole("button", { name: w.show })).toBeNull();
    expect(within(rowFor("Harbour sketch")).getByText(w.waitingPlayer)).toBeTruthy();
  });

  it("lets a player take their own map back, and says so", async () => {
    render(<Maps api={mockApi("table")} refId={PLAYER} />);
    await screen.findByText("Harbour sketch");
    fireEvent.click(within(rowFor("Harbour sketch")).getByRole("button", { name: w.takeBack }));
    await screen.findByText(w.takenBack);
    expect(screen.queryByText("Harbour sketch")).toBeNull();
  });

  it("never gives a player the DM's buttons, and a second press while one waits is ignored", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={PLAYER} />);
    await screen.findByText("Harbour sketch");
    for (const name of [w.useYes, w.useNo, w.hide, w.show, w.removeIt]) expect(screen.queryByRole("button", { name })).toBeNull();
    const button = within(rowFor("Harbour sketch")).getByRole("button", { name: w.takeBack });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText(w.takenBack);
    expect(api.calls.filter((c) => c.startsWith("changeMaps:")).length).toBe(1);
  });

  it("says what a changed refusal means: a press, an Undo, and a take-back's Undo", async () => {
    const api = mockApi("table");
    const real = api.changeMaps!.bind(api);
    let refuse = false;
    api.changeMaps = async (ref, change) => {
      if (refuse) throw new ApiError("changed");
      return real(ref, change);
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    refuse = true;
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.justChanged);
    refuse = false;
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.doneYes);
    refuse = true;
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    await screen.findByText(w.undoFailed);
    refuse = false;
    fireEvent.click(within(rowFor("Harbour sketch")).getByRole("button", { name: w.takeBack }));
    await screen.findByText(w.takenBack);
    refuse = true;
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    await screen.findByText(w.takeBackUndoFailed);
  });

  it("the DM's empty list names the form and Discord; a player's empty list is unchanged", async () => {
    const api = mockApi("table");
    api.maps = async () => ({ canManage: true, maps: [] });
    render(<Maps api={api} refId={DM} />);
    expect(await screen.findByText("No maps yet. Add one with the form, or post one in Discord and it shows up here.")).toBeTruthy();
    cleanup();
    const player = mockApi("table");
    player.maps = async () => ({ canManage: false, maps: [] });
    render(<Maps api={player} refId={PLAYER} />);
    expect(await screen.findByText("No maps have been shown to the table yet.")).toBeTruthy();
  });

  it("says it could not load, with a way to try again", async () => {
    const api = mockApi("table");
    api.maps = async () => {
      throw new Error("down");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText(w.failed);
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("shows the working stage while a press waits, and nothing once it is done", async () => {
    const api = mockApi("table");
    const real = api.changeMaps!.bind(api);
    let release!: () => void;
    api.changeMaps = async (ref, change) => {
      await new Promise<void>((r) => (release = r));
      return real(ref, change);
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.workingStage);
    expect((within(rowFor("Cave")).getByRole("button", { name: w.useYes }) as HTMLButtonElement).disabled).toBe(true);
    release();
    await screen.findByText(w.doneYes);
    expect(screen.queryByText(w.workingStage)).toBeNull();
  });

  it("says it is taking longer after 120 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = mockApi("table");
      const real = api.changeMaps!.bind(api);
      let release!: () => void;
      api.changeMaps = async (ref, change) => {
        await new Promise<void>((r) => (release = r));
        return real(ref, change);
      };
      render(<Maps api={api} refId={DM} />);
      await screen.findByText("Cave");
      fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
      await screen.findByText(w.workingStage);
      await vi.advanceTimersByTimeAsync(119_000);
      expect(screen.queryByText(w.stuck)).toBeNull(); // 119 seconds: still working
      await vi.advanceTimersByTimeAsync(1_000);
      expect(await screen.findByText(w.stuck)).toBeTruthy(); // 120 seconds: taking longer
      release();
      await screen.findByText(w.doneYes);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a refusal \"gone\" says the picture is not kept any more", async () => {
    const api = mockApi("table");
    api.changeMaps = async () => {
      throw new ApiError("gone");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.gone);
  });

  it("a refusal \"not-allowed\" says only DMs can answer", async () => {
    const api = mockApi("table");
    api.changeMaps = async () => {
      throw new ApiError("not-allowed");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.dmOnly);
  });

  it("any other failure says nothing was changed and to press again", async () => {
    const api = mockApi("table");
    api.changeMaps = async () => {
      throw new ApiError("server");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.wrong);
  });

  it("the list is read again after a refusal", async () => {
    const api = mockApi("table");
    api.changeMaps = async () => {
      throw new ApiError("gone");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    fireEvent.click(within(rowFor("Cave")).getByRole("button", { name: w.useYes }));
    await screen.findByText(w.gone);
    await waitFor(() => expect(api.calls.filter((c) => c === `maps:${DM}`).length).toBe(2));
  });

  it("a player never gets the DM's buttons, also after a failed load and a retry", async () => {
    const api = mockApi("table");
    const real = api.maps!.bind(api);
    let fail = true;
    api.maps = async (ref) => {
      if (fail) {
        fail = false;
        throw new Error("down");
      }
      return real(ref);
    };
    render(<Maps api={api} refId={PLAYER} />);
    await screen.findByText(w.failed);
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    await screen.findByText("Harbour sketch");
    for (const name of [w.useYes, w.useNo, w.show, w.hide, w.removeIt]) expect(screen.queryByRole("button", { name })).toBeNull();
  });
});

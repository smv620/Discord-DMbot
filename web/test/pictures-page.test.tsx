/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Pictures page (#3276): the switch, the tiles and their link, the game master's and the poster's buttons, delete
// with a confirm, the permanent limit, the filters and "Show more". The switch is mocked so each test can turn it on.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AccountApi, type PictureActionLook, type PictureItem } from "../src/account/api";
import { keepDmOnlyMarks } from "../src/account/api-campaign";
import { mockApi } from "../src/account/mock";
import Pictures, { WAITING_RECHECK_MS } from "../src/account/Pictures";
import { picturesPage as w } from "../src/content/pictures-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/pictures-switch", () => ({
  get PICTURES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const PATH = "/account/pictures/100.cmp1";
const PLAYER_PATH = "/account/pictures/100.cmp1.player";
const alts = (): string[] => Array.from(document.querySelectorAll("img")).map((img) => img.getAttribute("alt") ?? "");
const tileFor = (description: string): HTMLElement => {
  const img = screen.getByAltText(description);
  return img.closest("li") as HTMLElement;
};

describe("the Pictures page", () => {
  it("says it is not open yet while the switch is off, and asks for nothing", () => {
    live.on = false;
    const api = mockApi("table");
    render(<Pictures api={api} path={PATH} />);
    expect(screen.getByText(w.notOpen)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("pictures:"))).toBe(false);
  });

  it("a game master sees the tiles newest first, with the date, session and poster", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    expect(alts()).toEqual(["A map of the Brass Ford", "Cerric's oath scroll", "A storm over the ford"]);
    expect(tileFor("A map of the Brass Ford").textContent).toContain("Session 2");
    expect(tileFor("A map of the Brass Ford").textContent).toContain("Mira");
  });

  it("a tile's link opens the picture in a new tab with rel noopener", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    const link = tileFor("A map of the Brass Ford").querySelector("a") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/pictures/pic-1");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener");
  });

  it("the usage line reads like 312 MB of 500 MB used, for a game master only", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    expect(await screen.findByText(w.usage(312, 500, 100, 400))).toBeTruthy();
    expect(screen.getByText(w.roomNote)).toBeTruthy();
  });

  it("a player sees no Permanent button, and the poster sees Make private and Delete only on their own", async () => {
    render(<Pictures api={mockApi("table")} path={PLAYER_PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    expect(screen.queryByRole("button", { name: w.permanent })).toBeNull();
    expect(screen.queryByText(w.usage(312, 500, 100, 400))).toBeNull();
    expect(screen.queryByText(w.roomNote)).toBeNull();
    // Mira's own pictures have the two buttons; Cerric's have none.
    expect(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate })).toBeTruthy();
    expect(within(tileFor("Cerric's oath scroll")).queryByRole("button", { name: w.remove })).toBeNull();
  });

  it("Delete asks once, and Cancel keeps the picture", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.remove }));
    expect(screen.getByText(w.confirmDelete)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.cancel }));
    expect(screen.queryByText(w.confirmDelete)).toBeNull();
    expect(alts()).toContain("A map of the Brass Ford");
  });

  it("confirming Delete removes the tile", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    const tile = tileFor("A map of the Brass Ford");
    fireEvent.click(within(tile).getByRole("button", { name: w.remove }));
    expect(within(tile).getByRole("button", { name: "Yes, delete it" })).toBeTruthy();
    fireEvent.click(within(tile).getByRole("button", { name: w.confirmRemove }));
    await waitFor(() => expect(alts()).not.toContain("A map of the Brass Ford"));
  });

  it("a permanent pick past the limit shows the full message with 400 MB, and the buttons still work", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A storm over the ford");
    fireEvent.click(screen.getByRole("button", { name: w.showMore }));
    await screen.findByAltText("A torn page from a diary");
    // pic-2 is permanent already; pic-1, pic-3 and pic-4 fill 400 MB, so pic-5 is refused.
    for (const description of ["A map of the Brass Ford", "A storm over the ford", "The ferryman's boat"]) {
      fireEvent.click(screen.getByRole("button", { name: w.makePermanent(description) }));
      await waitFor(() => expect(screen.getByRole("button", { name: w.stopPermanent(description) })).toBeTruthy());
    }
    fireEvent.click(screen.getByRole("button", { name: w.makePermanent("A torn page from a diary") }));
    expect(await screen.findByText(w.permanentFull(400))).toBeTruthy();
    expect((screen.getByRole("button", { name: w.makePermanent("A torn page from a diary") }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("the filters reload from the start: a poster narrows the grid", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    fireEvent.change(screen.getByLabelText(/Posted by/), { target: { value: "Cerric" } });
    await waitFor(() => expect(alts()).toEqual(["Cerric's oath scroll", "The ferryman's boat"]));
  });

  it("Show more adds the next page", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A storm over the ford");
    fireEvent.click(screen.getByRole("button", { name: w.showMore }));
    await waitFor(() => expect(alts()).toContain("A torn page from a diary"));
    // Six pictures in all for a DM: the DM-only one from a document is the oldest, so it is on this page too (#4761).
    expect(alts()).toHaveLength(6);
  });

  it("a waiting answer shows Working on it under the tile, and the list is asked again", async () => {
    const api = mockApi("table");
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A storm over the ford");
    fireEvent.click(screen.getByRole("button", { name: w.showMore }));
    await screen.findByAltText("A torn page from a diary");
    const before = api.calls.filter((c) => c.startsWith("pictures:")).length;
    // Deleting pic-5 answers "waiting" in the mock; the list is read again and no longer shows the picture.
    const tile = tileFor("A torn page from a diary");
    fireEvent.click(within(tile).getByRole("button", { name: w.remove }));
    fireEvent.click(within(tile).getByRole("button", { name: w.confirmRemove }));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("pictures:")).length).toBeGreaterThan(before));
    await waitFor(() => expect(alts()).not.toContain("A torn page from a diary"));
    expect(screen.queryByText(w.failed)).toBeNull();
  });

  it("a search narrows the grid, and an empty result says so", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    fireEvent.input(screen.getByLabelText(/Search the descriptions/), { target: { value: "map" } });
    fireEvent.click(screen.getByRole("button", { name: w.search }));
    // "map" also finds the DM-only picture from a document: a DM sees it in the search too (#4761).
    await waitFor(() => expect(alts()).toEqual(["A map of the Brass Ford", "A map from the Keep's handout"]));
    fireEvent.input(screen.getByLabelText(/Search the descriptions/), { target: { value: "zzz" } });
    fireEvent.click(screen.getByRole("button", { name: w.search }));
    expect(await screen.findByText(w.noMatch)).toBeTruthy();
  });

  it("private and permanent tiles are marked", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A storm over the ford");
    expect(tileFor("A storm over the ford").textContent).toContain(w.privateTag);
    expect(tileFor("Cerric's oath scroll").textContent).toContain(w.permanentTag);
  });
});

describe("the recheck after a waiting answer (#3322)", () => {
  // A stub whose one tile flips to private on the read number `changeAfterReads` (later reads see the change).
  function waitingApi(changeAfterReads: number) {
    let reads = 0;
    const tile = (isPublic: boolean): PictureItem => ({
      code: "pic-1",
      cursor: "1791004000:1",
      at: 1791004000,
      sessionNumber: 2,
      poster: "Mira",
      description: "A map of the Brass Ford",
      previewUrl: "/pictures/preview-1.webp",
      permanent: false,
      isPublic,
      mine: true,
    });
    const pictures = vi.fn(async () => {
      reads += 1;
      return {
        pictures: [tile(reads <= changeAfterReads)],
        more: false,
        isDm: true,
        usage: null,
        sessions: [2],
        posters: ["Mira"],
      };
    });
    const pictureAction = vi.fn(async () => ({ state: "waiting" as const, note: "" }));
    return { pictures, pictureAction } as unknown as AccountApi & {
      pictures: ReturnType<typeof vi.fn>;
      pictureAction: ReturnType<typeof vi.fn>;
    };
  }
  async function passTime(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks the list once more after 10 seconds and clears Working on it when the change shows", async () => {
    const api = waitingApi(2);
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
    expect(api.pictures).toHaveBeenCalledTimes(2);
    await passTime(WAITING_RECHECK_MS);
    expect(api.pictures).toHaveBeenCalledTimes(3);
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.waitingStage)).toBeNull();
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.privateTag)).toBeTruthy();
  });

  it("no extra read is made when the tile is no longer waiting", async () => {
    const api = waitingApi(1);
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    const atAction = api.pictures.mock.calls.length;
    await passTime(WAITING_RECHECK_MS);
    expect(api.pictures.mock.calls.length).toBe(atAction);
  });

  it("an action that answers after the page is left schedules no read", async () => {
    const api = waitingApi(2);
    let answer!: (value: { state: "waiting"; note: string }) => void;
    api.pictureAction.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    const { unmount } = render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    unmount();
    await act(async () => {
      answer({ state: "waiting", note: "" });
    });
    const atLeaving = api.pictures.mock.calls.length;
    await passTime(WAITING_RECHECK_MS);
    expect(api.pictures.mock.calls.length).toBe(atLeaving);
  });

  it("shows how long a picture action has waited, under the stage", async () => {
    const api = waitingApi(2);
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
    await passTime(5_000);
    expect(within(tileFor("A map of the Brass Ford")).getByText(/Running for/)).toBeTruthy();
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.stuck)).toBeNull();
  });

  it("says it is stuck after two minutes, with what to do", async () => {
    const api = waitingApi(1000); // the tile never changes, so the action keeps waiting
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    await passTime(119_000);
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.stuck)).toBeNull();
    await passTime(2_000);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.stuck)).toBeTruthy();
  });

  it("reads the list on one rhythm while a tile waits, and stops when none does", async () => {
    let shown = false; // the change arrives only when the test says so
    const api = waitingApi(1000);
    const base = api.pictures.getMockImplementation() as unknown as () => Promise<{ pictures: PictureItem[] }>;
    api.pictures.mockImplementation(async () => {
      const got = await base();
      if (shown) got.pictures = got.pictures.map((t) => ({ ...t, isPublic: false }));
      return got;
    });
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    const atAction = api.pictures.mock.calls.length;
    await passTime(3 * WAITING_RECHECK_MS);
    expect(api.pictures.mock.calls.length).toBe(atAction + 3);
    shown = true;
    await passTime(WAITING_RECHECK_MS);
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.waitingStage)).toBeNull();
    const atStop = api.pictures.mock.calls.length;
    await passTime(3 * WAITING_RECHECK_MS);
    expect(api.pictures.mock.calls.length).toBe(atStop);
  });

  it("keeps another tile's failure on the page through a read of the rhythm", async () => {
    const tile = (code: string, poster: string, alt: string): PictureItem => ({
      code,
      cursor: `1791004000:${code}`,
      at: 1791004000,
      sessionNumber: 2,
      poster,
      description: alt,
      previewUrl: `/pictures/preview-${code}.webp`,
      permanent: false,
      isPublic: true,
      mine: true,
    });
    const pictures = vi.fn(async () => ({
      pictures: [tile("pic-1", "Mira", "A map of the Brass Ford"), tile("pic-2", "Brannoch", "A torn page from a diary")],
      more: false,
      isDm: true,
      usage: null,
      sessions: [2],
      posters: ["Mira", "Brannoch"],
    }));
    const pictureAction = vi.fn(async (_ref: string, code: string) => {
      if (code === "pic-2") throw new Error("boom");
      return { state: "waiting" as const, note: "" };
    });
    const api = { pictures, pictureAction } as unknown as AccountApi;
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    fireEvent.click(within(tileFor("A torn page from a diary")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    expect(within(tileFor("A torn page from a diary")).getByText(w.actionFailed)).toBeTruthy();
    await passTime(WAITING_RECHECK_MS);
    expect(within(tileFor("A torn page from a diary")).getByText(w.actionFailed)).toBeTruthy();
  });

  it("no extra read is made while the tab is hidden", async () => {
    const api = waitingApi(2);
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    const atAction = api.pictures.mock.calls.length;
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    try {
      await passTime(WAITING_RECHECK_MS);
      expect(api.pictures.mock.calls.length).toBe(atAction);
    } finally {
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
    }
  });

  it("no extra read is made after the page is left", async () => {
    const api = waitingApi(2);
    const { unmount } = render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
    unmount();
    const atLeaving = api.pictures.mock.calls.length;
    await passTime(WAITING_RECHECK_MS);
    expect(api.pictures.mock.calls.length).toBe(atLeaving);
  });
});

// A picture DMbot read out of an uploaded document (#4761): the mark, where it came from, no Make public or private, and never a player's.
const DM_ONLY = "A map from the Keep's handout";
// The DM-only picture is the oldest of six, so it is on the second page: press Show more until it shows.
async function dmOnlyTile(): Promise<HTMLElement> {
  render(<Pictures api={mockApi("table")} path={PATH} />);
  for (let i = 0; i < 4; i++) {
    await waitFor(() => expect(alts().length).toBeGreaterThan(0));
    const img = Array.from(document.querySelectorAll("img")).find((x) => x.getAttribute("alt") === DM_ONLY);
    if (img) return img.closest("li") as HTMLElement;
    const before = document.querySelectorAll("img").length;
    fireEvent.click(await screen.findByRole("button", { name: w.showMore }));
    await waitFor(() => expect(document.querySelectorAll("img").length).toBeGreaterThan(before));
  }
  throw new Error("the DM-only picture never showed");
}

describe("DM-only pictures from documents", () => {
  it("a DM only picture shows the mark and where it came from", async () => {
    const tile = await dmOnlyTile();
    expect(within(tile).getByText(w.dmOnlyTag, { exact: false })).toBeTruthy();
    expect(within(tile).getByText(w.fromUpload("The Sunken Keep"))).toBeTruthy();
    expect(within(tile).queryByText(w.privateTag)).toBeNull();
  });

  it("a DM only picture has no Make public and no Make private", async () => {
    const tile = await dmOnlyTile();
    expect(within(tile).queryByRole("button", { name: w.makePublic })).toBeNull();
    expect(within(tile).queryByRole("button", { name: w.makePrivate })).toBeNull();
  });

  it("a picture without the field looks as before", async () => {
    render(<Pictures api={mockApi("table")} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    const tile = tileFor("A map of the Brass Ford");
    expect(within(tile).getByRole("button", { name: w.makePrivate })).toBeTruthy();
    expect(within(tile).queryByText(w.dmOnlyTag, { exact: false })).toBeNull();
  });

  it("the label is dropped when it is not plain text", () => {
    const base: PictureItem = {
      code: "pic-x", cursor: "1:1", at: 1, sessionNumber: null, poster: "DMbot", description: "A map", previewUrl: "/p.webp",
      permanent: false, isPublic: false, mine: false, dmOnly: true,
    };
    expect(keepDmOnlyMarks({ ...base, uploadLabel: "<b>The Keep</b>" }).uploadLabel).toBeUndefined();
    expect(keepDmOnlyMarks({ ...base, uploadLabel: "" }).uploadLabel).toBeUndefined();
    expect(keepDmOnlyMarks({ ...base, uploadLabel: "The Keep" }).uploadLabel).toBe("The Keep");
    expect(keepDmOnlyMarks({ ...base, dmOnly: "yes" as unknown as boolean }).dmOnly).toBeUndefined();
  });

  it("a player's view never lists a DM only picture", async () => {
    render(<Pictures api={mockApi("table")} path={PLAYER_PATH} />);
    await waitFor(() => expect(alts().length).toBeGreaterThan(0));
    expect(alts()).not.toContain(DM_ONLY);
  });
});

describe("following a waiting action by its id (#3355, route 6)", () => {
  const tile = (): PictureItem => ({
    code: "pic-1",
    cursor: "1791004000:1",
    at: 1791004000,
    sessionNumber: 2,
    poster: "Mira",
    description: "A map of the Brass Ford",
    previewUrl: "/pictures/preview-1.webp",
    permanent: false,
    isPublic: true,
    mine: true,
  });
  // The list never shows the change, so only the action's own look can end the wait.
  function followApi(looks: Partial<PictureActionLook>[]) {
    let at = 0;
    const pictures = vi.fn(async () => ({ pictures: [tile()], more: false, isDm: true, usage: null, sessions: [2], posters: ["Mira"] }));
    const pictureAction = vi.fn(async () => ({ state: "waiting" as const, note: "", id: "act-1" }));
    const pictureActionLook = vi.fn(async () => {
      const now = Math.floor(Date.now() / 1000);
      const look = looks[Math.min(at++, looks.length - 1)] ?? {};
      return { id: "act-1", action: "make-private", state: "working", stage: "working", note: "", startedAt: now - 30, updatedAt: now - 10, serverNow: now, stalled: false, ...look } as PictureActionLook;
    });
    return { pictures, pictureAction, pictureActionLook } as unknown as AccountApi & { pictureActionLook: ReturnType<typeof vi.fn> };
  }
  async function passTime(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }
  afterEach(() => {
    vi.useRealTimers();
  });
  async function press(api: AccountApi) {
    render(<Pictures api={api} path={PATH} />);
    await screen.findByAltText("A map of the Brass Ford");
    vi.useFakeTimers();
    fireEvent.click(within(tileFor("A map of the Brass Ford")).getByRole("button", { name: w.makePrivate }));
    await passTime(0);
  }

  it("asks how the action stands and says DMbot is working on it when the bot has taken it", async () => {
    const api = followApi([{ state: "working" }]);
    await press(api);
    expect(api.pictureActionLook).toHaveBeenCalledWith("100.cmp1", "act-1");
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.workingStage)).toBeTruthy();
    expect(within(tileFor("A map of the Brass Ford")).getByText(/Running for/)).toBeTruthy();
  });

  it("says waiting while the bot has not taken it", async () => {
    const api = followApi([{ state: "waiting", stage: "waiting" }]);
    await press(api);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.workingStage)).toBeNull();
  });

  it("says it is stuck, with what to do, when the server calls it stalled", async () => {
    const api = followApi([{ state: "working", stalled: true }]);
    await press(api);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.stuck)).toBeTruthy();
  });

  it("a refused action ends the wait and shows the refusal's own words", async () => {
    const api = followApi([{ state: "refused", note: "Only the person who posted it or a DM can do that." }]);
    await press(api);
    expect(await screen.findByText("Only the person who posted it or a DM can do that.")).toBeTruthy();
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.waitingStage)).toBeNull();
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.workingStage)).toBeNull();
  });

  it("a failed action with no words says the page's own failure line", async () => {
    const api = followApi([{ state: "failed", note: "" }]);
    await press(api);
    expect(await screen.findByText(w.actionFailed)).toBeTruthy();
  });

  it("a done action ends the wait and reads the list again", async () => {
    const api = followApi([{ state: "done" }]);
    await press(api);
    const reads = (api.pictures as unknown as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(reads).toBeGreaterThanOrEqual(3); // the first read, the one after the press, and the one after "done"
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.workingStage)).toBeNull();
  });

  it("a look that worked and then fails goes back to the page's own wait", async () => {
    const api = followApi([{ state: "working" }]);
    await press(api);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.workingStage)).toBeTruthy();
    (api.pictureActionLook as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("down"));
    await passTime(WAITING_RECHECK_MS);
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.workingStage)).toBeNull();
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
  });

  it("a look that fails changes nothing: the wait stays and the list read goes on", async () => {
    const api = followApi([{ state: "working" }]);
    (api.pictureActionLook as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("down"));
    await press(api);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
    await passTime(WAITING_RECHECK_MS);
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.waitingStage)).toBeTruthy();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Pictures page (#3276): the switch, the tiles and their link, the game master's and the poster's buttons, delete
// with a confirm, the permanent limit, the filters and "Show more". The switch is mocked so each test can turn it on.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AccountApi, type PictureItem } from "../src/account/api";
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
    expect(alts()).toHaveLength(5);
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
    await waitFor(() => expect(alts()).toEqual(["A map of the Brass Ford"]));
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
    expect(within(tileFor("A map of the Brass Ford")).getByText(w.working)).toBeTruthy();
    expect(api.pictures).toHaveBeenCalledTimes(2);
    await passTime(WAITING_RECHECK_MS);
    expect(api.pictures).toHaveBeenCalledTimes(3);
    expect(within(tileFor("A map of the Brass Ford")).queryByText(w.working)).toBeNull();
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

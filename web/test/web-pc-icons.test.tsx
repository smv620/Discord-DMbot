/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Player character icons on the Story Graph (#3622, behind PC_ICONS_LIVE): the picture clipped in a PC's circle, 👤 when
// there is none or it does not load, nothing when hidden; the Icon row with each choice and one Undo, offered only to the
// DM and the PC's own player; and nothing at all while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type PictureItem, type WebGraph, type WebNode } from "../src/account/api";
import { withPictureIcons } from "../src/account/api-names";
import PcIconRow, { pickable, pictureId } from "../src/account/PcIconRow";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import WebMap from "../src/account/WebMap";
import { pcIconWords as w } from "../src/content/pc-icon-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/pc-icons-switch", () => ({
  get PC_ICONS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const pc = (over: Partial<WebNode> = {}): WebNode => ({ id: "p1", name: "Aldric", kind: "player_character", icon: { kind: "discord", url: "data:image/svg+xml;utf8,x" }, iconChoice: "default", canSetIcon: true, iconHas: { discord: true, dndbeyond: true }, ...over });
const drawn = (node: WebNode) =>
  render(<WebMap nodes={[{ id: "m", name: "Mira", kind: "npc" }, node]} edges={[]} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} onIcon={async () => null} />);

describe("the icon in the circle", () => {
  it("draws the picture with alt text 'Icon of <PC>'", () => {
    drawn(pc());
    const img = document.querySelector(".web-pc-icon image") as SVGElement;
    expect(img.getAttribute("href")).toContain("data:image");
    expect(img.getAttribute("aria-label")).toBe(w.alt("Aldric"));
    expect(document.querySelector(".web-pc-icon-border")).toBeTruthy();
  });

  it("draws 👤 when there is no icon", () => {
    drawn(pc({ icon: null }));
    expect(document.querySelector(".web-pc-icon image")).toBeNull();
    expect(document.querySelector(".web-pc-person")?.textContent).toBe("👤");
  });

  it("falls back to 👤 when the picture does not load", () => {
    drawn(pc());
    fireEvent.error(document.querySelector(".web-pc-icon image") as SVGElement);
    expect(document.querySelector(".web-pc-icon image")).toBeNull();
    expect(document.querySelector(".web-pc-person")?.textContent).toBe("👤");
  });

  it("shows no picture at all when hidden", () => {
    drawn(pc({ icon: { kind: "hidden", url: null }, iconChoice: "hidden" }));
    expect(document.querySelector(".web-pc-icon")).toBeNull();
    expect(document.querySelector(".web-pc-person")).toBeNull();
  });

  it("draws nothing for a name that is not a player character", () => {
    drawn(pc({ kind: "npc" }));
    expect(document.querySelector(".web-pc-icon, .web-pc-person")).toBeNull();
  });

  it("draws nothing while the switch is off", () => {
    live.on = false;
    drawn(pc());
    expect(document.querySelector(".web-pc-icon, .web-pc-person")).toBeNull();
  });
});

describe("the Icon row on the page", () => {
  const path = "/account/web/100.cmp1.icons";
  async function open(name = "Mira Voss") {
    const api = mockApi("table");
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click((await screen.findAllByText(new RegExp(name), { selector: "text" }))[0] as HTMLElement);
    return api;
  }
  const rowOf = async () => (await screen.findByRole("group", { name: w.heading }, { timeout: 8000 })) as HTMLElement;

  it("offers only the pictures that exist, and a hide, to the DM", async () => {
    await open("Mira Voss");
    const row = await rowOf();
    expect(within(row).getByRole("button", { name: w.useDiscord })).toBeTruthy();
    expect(within(row).getByRole("button", { name: w.hide })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: w.remove })).toBeNull(); // (already the usual order)
  });

  it("each choice is sent, and one Undo puts the old choice back", async () => {
    const api = await open("Mira Voss");
    let row = await rowOf();
    fireEvent.click(within(row).getByRole("button", { name: w.hide }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith(":n1:hidden"))).toBe(true));
    row = await rowOf();
    fireEvent.click(await within(row).findByRole("button", { name: w.undoLabel }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith(":n1:default"))).toBe(true));
  });

  it("says plainly when a picture is not there any more", async () => {
    const api = mockApi("table");
    api.setPcIcon = async () => {
      throw new ApiError("unavailable");
    };
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click((await screen.findAllByText(/Mira Voss/, { selector: "text" }))[0] as HTMLElement);
    const row = (await screen.findByRole("group", { name: w.heading }, { timeout: 8000 })) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: w.useDiscord }));
    expect(await within(row).findByText(w.problems.unavailable as string)).toBeTruthy();
  });

  it("offers no button for a picture that does not exist", async () => {
    const api = await open("Aldric Thorn");
    const row = await rowOf();
    expect(within(row).queryByRole("button", { name: w.useDiscord })).toBeNull(); // (this one has no Discord picture)
    expect(api.calls.some((c) => c.includes("setPcIcon"))).toBe(false);
  });

  it("is not offered to someone who may not change it", async () => {
    const api = mockApi("table");
    api.webGraph = async (): Promise<WebGraph> => ({ center: "m", nodes: [{ id: "m", name: "Mira", kind: "npc" }, pc({ canSetIcon: false })], edges: [], truncated: false, maxHops: 2, canEdit: false, view: "players" });
    api.webSize = async () => ({ names: 2 });
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click(await screen.findByText("Aldric", { selector: "text" }));
    expect(screen.queryByRole("group", { name: w.heading })).toBeNull();
  });
});

describe("the real door", () => {
  it("reads a 409 unavailable as a picture that is gone, and posts the choice to the character's own address", async () => {
    const { httpApi } = await import("../src/account/api");
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, body: String(init.body ?? "") });
      return new Response(JSON.stringify({ error: "unavailable" }), { status: 409, headers: { "content-type": "application/json" } });
    });
    const api = httpApi("/api");
    await expect(api.setPcIcon!("100.cmp1", "e1", "discord")).rejects.toMatchObject({ kind: "unavailable" });
    expect(calls[0]!.url).toBe("/api/me/campaigns/100.cmp1/pc-icons/e1");
    expect(JSON.parse(calls[0]!.body)).toEqual({ choice: "discord" });
    vi.unstubAllGlobals();
  });
});

describe("Set a picture (#3622, core #5280)", () => {
  const path = "/account/web/100.cmp1.icons";
  const pic = (n: number, over: Partial<PictureItem> = {}): PictureItem => ({ code: `c${n}`, cursor: `1700000${n}:${n}`, at: 1700000 + n, sessionNumber: null, poster: "Mira", description: `Picture ${n}`, previewUrl: `https://api.example.com/p${n}.webp`, permanent: false, isPublic: false, mine: false, ...over });

  it("takes the numeric id from the gallery cursor, and never offers a DM's file picture or, to a player, another's", () => {
    expect(pictureId(pic(7))).toBe(7);
    const list = [pic(1), pic(2, { dmOnly: true }), pic(3, { mine: true })];
    expect(pickable(list, true).map(pictureId)).toEqual([1, 3]);
    expect(pickable(list, false).map(pictureId)).toEqual([3]);
  });

  it("makes a picture icon's path absolute, and leaves every other icon alone", () => {
    const graph = { center: "a", nodes: [{ id: "a", name: "A", kind: "player_character", icon: { kind: "picture", url: "/me/campaigns/1/pc-icons/a/picture" } }, { id: "b", name: "B", kind: "player_character", icon: { kind: "discord", url: "https://cdn.example.com/x.png" } }], edges: [], truncated: false, maxHops: 2, canEdit: true, view: "players" } as WebGraph;
    const out = withPictureIcons(graph, "https://api.example.com");
    expect(out.nodes[0]!.icon!.url).toBe("https://api.example.com/me/campaigns/1/pc-icons/a/picture");
    expect(out.nodes[1]!.icon!.url).toBe("https://cdn.example.com/x.png");
  });

  it("lists the campaign's pictures, sends the one picked with its id, and Undo sends the old choice back", async () => {
    const api = mockApi("table");
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click((await screen.findAllByText(/Mira Voss/, { selector: "text" }))[0] as HTMLElement);
    const row = (await screen.findByRole("group", { name: w.heading }, { timeout: 8000 })) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: w.setPicture }));
    const use = await within(row).findAllByRole("button", { name: new RegExp(w.usePicture) });
    fireEvent.click(use[0] as HTMLElement);
    await waitFor(() => expect(api.calls.some((c) => /:n1:custom:\d+$/.test(c))).toBe(true));
    const again = (await screen.findByRole("group", { name: w.heading })) as HTMLElement;
    fireEvent.click(await within(again).findByRole("button", { name: w.undoLabel }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith(":n1:default"))).toBe(true));
  });

  it("Undo of a change from one picture to another puts the first picture back, and keeps reading pages until one can be picked", async () => {
    const api = mockApi("table");
    await api.setPcIcon!("100.cmp1.icons", "n1", "custom", 11);
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click((await screen.findAllByText(/Mira Voss/, { selector: "text" }))[0] as HTMLElement);
    const row = (await screen.findByRole("group", { name: w.heading }, { timeout: 8000 })) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: w.setPicture }));
    fireEvent.click((await within(row).findAllByRole("button", { name: new RegExp(w.usePicture) }))[0] as HTMLElement);
    await waitFor(() => expect(api.calls.filter((c) => /:n1:custom:\d+$/.test(c)).length).toBe(2));
    const again = (await screen.findByRole("group", { name: w.heading })) as HTMLElement;
    fireEvent.click(await within(again).findByRole("button", { name: w.undoLabel }));
    await waitFor(() => expect(api.calls.filter((c) => /:n1:custom:\d+$/.test(c)).pop()).toMatch(/:custom:11$/));
  });

  it("reads on past a page with nothing a player may pick", async () => {
    const api = mockApi("table");
    const pages = [{ pictures: [pic(1)], more: true }, { pictures: [pic(2, { mine: true })], more: false }];
    let n = 0;
    api.pictures = async () => ({ ...pages[n++]!, isDm: false, usage: null, sessions: [], posters: [] });
    render(<PcIconRow node={pc({ iconHas: { discord: true, dndbeyond: true, pictures: true } })} api={api} refId="1.a" onChoose={async () => null} undo={false} onUndo={async () => null} />);
    fireEvent.click(screen.getByRole("button", { name: w.setPicture }));
    expect((await screen.findAllByRole("button", { name: new RegExp(w.usePicture) })).length).toBe(1);
  });

  it("offers no picker when the campaign has no pictures to pick", () => {
    render(<PcIconRow node={pc({ iconHas: { discord: true, dndbeyond: true, pictures: false } })} api={mockApi("table")} refId="1.a" onChoose={async () => null} undo={false} onUndo={async () => null} />);
    expect(screen.queryByRole("button", { name: w.setPicture })).toBeNull();
  });

  it("says so when none of the pictures may be picked", async () => {
    const api = mockApi("table");
    api.pictures = async () => ({ pictures: [pic(1, { dmOnly: true })], more: false, isDm: true, usage: null, sessions: [], posters: [] });
    render(<PcIconRow node={pc({ iconHas: { discord: true, dndbeyond: true, pictures: true } })} api={api} refId="1.a" onChoose={async () => null} undo={false} onUndo={async () => null} />);
    fireEvent.click(screen.getByRole("button", { name: w.setPicture }));
    expect(await screen.findByText(w.noPictures)).toBeTruthy();
  });

  it("posts the picture's id with the choice", async () => {
    const { httpApi } = await import("../src/account/api");
    const bodies: string[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body ?? ""));
      return new Response(JSON.stringify({ previous: "default", now: "custom", previousPicture: null }), { status: 200, headers: { "content-type": "application/json" } });
    });
    await httpApi("/api").setPcIcon!("100.cmp1", "e1", "custom", 42);
    expect(JSON.parse(bodies[0]!)).toEqual({ choice: "custom", picture: 42 });
    vi.unstubAllGlobals();
  });
});

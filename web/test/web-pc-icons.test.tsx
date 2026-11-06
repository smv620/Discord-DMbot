/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Player character icons on the Story Graph (#3622, behind PC_ICONS_LIVE): the picture clipped in a PC's circle, 👤 when
// there is none or it does not load, nothing when hidden; the Icon row with each choice and one Undo, offered only to the
// DM and the PC's own player; and nothing at all while the switch is off.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type WebGraph, type WebNode } from "../src/account/api";
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
    await screen.findByText(/names?, \d+ lines?/);
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
    fireEvent.click(await within(row).findByRole("button", { name: w.undo }));
    await waitFor(() => expect(api.calls.some((c) => c.endsWith(":n1:default"))).toBe(true));
  });

  it("says plainly when a picture is not there any more", async () => {
    const api = mockApi("table");
    api.setPcIcon = async () => {
      throw new ApiError("unavailable");
    };
    render(<Web api={api} path={path} />);
    await screen.findByText(/names?, \d+ lines?/);
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
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.click(await screen.findByText("Aldric", { selector: "text" }));
    expect(screen.queryByRole("group", { name: w.heading })).toBeNull();
  });
});

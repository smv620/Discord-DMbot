/** @jsxImportSource preact */
// @vitest-environment happy-dom
// People the story never named, on the Story Graph (#3732, behind UNNAMED_MAP_LIVE): a dashed node under its label, a Graph
// settings kind, the Names page's own panel with one Undo, the line for the ones still waiting, and nothing at all while the
// switch is off or for anyone but a DM in the Full View.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { notNamedWords as nn } from "../src/content/not-named-words";
import { webPage as words } from "../src/content/web-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/unnamed-map-switch", () => ({
  get UNNAMED_MAP_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const path = "/account/web/100.cmp1.unnamed";
const label = "Ken's sister (name unknown)";
async function open() {
  const api = mockApi("table");
  render(<Web api={api} path={path} />);
  await screen.findByText(/Showing \d+ names? and \d+ connections?/);
  return api;
}

describe("people not named yet on the map", () => {
  it("draws the person dashed, under the label the story gives", async () => {
    await open();
    await waitFor(() => expect(document.querySelector(".web-node.web-unnamed")).toBeTruthy());
    expect(document.querySelector(".web-node.web-unnamed title")?.textContent).toBe(label);
  });

  it("says how many still wait, with a link to Names", async () => {
    await open();
    expect(screen.getByText(words.waitingUnnamed(2), { exact: false })).toBeTruthy();
    expect(screen.getByRole("link", { name: words.waitingUnnamedLink }).getAttribute("href")).toBe("/account/names/100.cmp1.unnamed");
  });

  it("has People not named yet as a kind in Graph settings, on by default", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: words.settingsButton }));
    const box = (await screen.findByLabelText(new RegExp(words.notNamedKind))) as HTMLInputElement;
    expect(box.checked).toBe(false); // (no kind ticked: every kind shows)
    fireEvent.click(box);
    await waitFor(() => expect(document.querySelector(".web-node.web-unnamed")).toBeTruthy());
  });

  it("opens the Names page's own panel, and gives one Undo after a name", async () => {
    const api = await open();
    fireEvent.click(await screen.findByRole("button", { name: /Ken's sister \(name unknown\)/, expanded: false }));
    fireEvent.click(await screen.findByRole("button", { name: words.changeFor(label) }));
    fireEvent.input(await screen.findByLabelText(nn.nameLabel), { target: { value: "Ilse" } });
    fireEvent.click(screen.getByRole("button", { name: nn.give }));
    expect(await screen.findByRole("button", { name: nn.undo })).toBeTruthy();
    expect(api.calls.some((c) => c.includes(":u1:") && c.includes('"name"'))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: nn.undo }));
    await screen.findByText(nn.undone);
    expect(api.calls.some((c) => c.includes("name-undo"))).toBe(true);
  });

  it("never draws one for a viewer who can't edit, even if the API sent it", async () => {
    const api = mockApi("table");
    const real = api.webGraph!.bind(api);
    api.webGraph = async (ref, query) => ({ ...(await real(ref, query)), canEdit: false });
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    expect(document.querySelector(".web-node.web-unnamed")).toBeNull();
    expect(screen.queryByText(words.waitingUnnamed(2), { exact: false })).toBeNull();
  });

  it("shows nothing of them while the switch is off", async () => {
    live.on = false;
    await open();
    expect(document.querySelector(".web-node.web-unnamed")).toBeNull();
    expect(screen.queryByText(words.waitingUnnamed(2), { exact: false })).toBeNull();
  });
});

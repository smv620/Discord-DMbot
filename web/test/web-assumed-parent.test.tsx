/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A place's assumed parent on the Names and Story Graph pages (#4304, website half of #4173 slice 3): the "(assumed)" mark and
// the DM's ✅ That's right, behind its switch. A player is never sent the mark, and the page drops it if the API sent it anyway.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph, WebLink, WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/assumed-parent-switch", () => ({
  get ASSUMED_PARENT_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const link = (over: Partial<WebLink> = {}): WebLink => ({ id: "l1", words: "is in", secret: false, direction: "a-b", assumed: true, ...over });
const plain = (): WebLink => ({ id: "l1", words: "is in", secret: false, direction: "a-b" }); // (the DM has said it is right)
const graph = (links: WebLink[], canEdit = true): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Gull's Rest", kind: "place" },
    { id: "n2", name: "Harbor Town", kind: "place" },
  ],
  edges: [{ a: "n1", b: "n2", label: links[0]!.words, more: links.length - 1, links }],
  truncated: false,
  maxHops: 2,
  canEdit,
  view: canEdit ? "full" : "players",
});
function pageWith(g: WebGraph, changes: WebLinkChange[] = []): AccountApi {
  const api = mockApi("table");
  api.webGraph = async () => g;
  api.webSize = async () => ({ names: 2 });
  api.changeWebLink = async (_ref, change) => {
    changes.push(change);
    return g.edges[0]!;
  };
  return api;
}
async function openPanel(api: AccountApi) {
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findByText(/Showing \d+ names? and \d+ connections?/);
  fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
  return await screen.findByRole("heading", { level: 2, name: /.+ and .+/ });
}

describe("an assumed parent", () => {
  it("shows the mark for the DM, and ✅ That's right sends one change and says what was noted", async () => {
    const changes: WebLinkChange[] = [];
    await openPanel(pageWith(graph([link()]), changes));
    expect(await screen.findByText(w.assumedMark)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.rightLabel(1) }));
    await waitFor(() => expect(changes).toEqual([{ action: "right", id: "l1" }]));
    expect(await screen.findByText(w.rightDone("Gull's Rest", "Harbor Town"))).toBeTruthy();
  });
  it("names the place and its parent the right way round when the line reads from the second name", async () => {
    const changes: WebLinkChange[] = [];
    await openPanel(pageWith(graph([link({ direction: "b-a" })]), changes));
    fireEvent.click(await screen.findByRole("button", { name: w.rightLabel(1) }));
    expect(await screen.findByText(w.rightDone("Harbor Town", "Gull's Rest"))).toBeTruthy();
  });
  it("shows neither the mark nor the button once it is the DM's own", async () => {
    await openPanel(pageWith(graph([plain()])));
    expect(screen.queryByText(w.assumedMark)).toBeNull();
    expect(screen.queryByRole("button", { name: w.rightLabel(1) })).toBeNull();
  });
  it("gives a player neither, even if the mark were sent", async () => {
    await openPanel(pageWith(graph([link()], false)));
    expect(screen.queryByText(w.assumedMark)).toBeNull();
    expect(screen.queryByRole("button", { name: w.rightLabel(1) })).toBeNull();
  });
  it("drops the mark for a peeking player, who is shown the full view but cannot edit", async () => {
    await openPanel(pageWith({ ...graph([link()], false), view: "full" }));
    expect(screen.queryByText(w.assumedMark)).toBeNull();
    expect(screen.queryByRole("button", { name: w.rightLabel(1) })).toBeNull();
  });
  it("shows today's panel while the switch is off", async () => {
    live.on = false;
    await openPanel(pageWith(graph([link()])));
    expect(screen.queryByText(w.assumedMark)).toBeNull();
    expect(screen.queryByRole("button", { name: w.rightLabel(1) })).toBeNull();
  });
});

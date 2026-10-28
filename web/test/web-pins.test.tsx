/** @jsxImportSource preact */
// @vitest-environment happy-dom
// 📌 Pin as 100% truth on the Story Graph (#2448, API #2447): a DM in the Full View sees which
// connections are pinned and can pin or unpin one with a single press; nobody else ever sees a pin
// (the Players View, a player, or one who peeks), even if the API sent it.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, WebGraph, WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const graph = (over: Partial<WebGraph> = {}): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Mira", kind: "npc" },
    { id: "n2", name: "Aldric", kind: "npc" },
  ],
  edges: [
    {
      a: "n1",
      b: "n2",
      label: "owes a debt to",
      more: 1,
      links: [
        { id: "l1", words: "owes a debt to", secret: false, direction: "a-b", pinned: true },
        { id: "l2", words: "trusts", secret: false, direction: "a-b" },
      ],
    },
  ],
  truncated: false,
  maxHops: 6,
  canEdit: true,
  ...over,
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

async function openPanel(api: AccountApi, suffix = "") {
  render(<Web api={api} path={`/account/web/100.cmp1${suffix}`} />);
  await screen.findByText(/names?, \d+ lines?/);
  fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
  return (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
}

describe("DMbot's check on a connection's card (#2859)", () => {
  const CHECK = "DMbot matched this name from your text.";
  const withCheck = (canEdit: boolean): WebGraph =>
    graph({
      canEdit,
      edges: [
        {
          a: "n1",
          b: "n2",
          label: "owes a debt to",
          more: 0,
          links: [{ id: "l1", words: "owes a debt to", secret: false, direction: "a-b", check: CHECK }],
        },
      ],
    });

  it("a connection's card shows DMbot's check to a DM", async () => {
    await openPanel(pageWith(withCheck(true)));
    expect(screen.getByText(w.checkLine(CHECK))).toBeTruthy();
  });

  it("the check is not kept for a player's view", async () => {
    await openPanel(pageWith(withCheck(false)));
    expect(screen.queryByText(w.checkLine(CHECK))).toBeNull();
  });
});

describe("a DM's pins", () => {
  it("shows which connection is pinned, and the legend says what 📌 means", async () => {
    const panel = await openPanel(pageWith(graph()));
    const rows = within(panel).getAllByRole("listitem");
    expect(within(rows[0]!).getByText(`${w.pinMark} ${w.pinnedWords}`)).toBeTruthy();
    expect(within(rows[1]!).queryByText(`${w.pinMark} ${w.pinnedWords}`)).toBeNull();
    expect(within(rows[1]!).getByText(w.pinHint)).toBeTruthy(); // said where the DM acts
    expect(screen.getByText(w.legendPinned)).toBeTruthy();
  });

  it("pins with one press, and says so", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph(), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.pinLabel(2) }));
    await waitFor(() => expect(changes).toEqual([{ action: "pin", id: "l2", pinned: true }]));
    expect(await screen.findByText(w.pinnedDone)).toBeTruthy();
  });

  it("unpins with one press, and says so", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph(), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.unpinLabel(1) }));
    await waitFor(() => expect(changes).toEqual([{ action: "pin", id: "l1", pinned: false }]));
    expect(await screen.findByText(w.unpinnedDone)).toBeTruthy();
  });

  it("says where Undo is", async () => {
    await openPanel(pageWith(graph()));
    expect(screen.getByText(w.undoNote)).toBeTruthy();
  });

  it("the panel has nothing serious for axe", async () => {
    const panel = await openPanel(pageWith(graph()));
    const result = await axe.run(panel, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

describe("nobody else sees a pin", () => {
  it("not in the Players View, even if the API sent one", async () => {
    const api = pageWith(graph());
    render(<Web api={api} path="/account/web/100.cmp1" />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(screen.queryByText(w.legendPinned)).toBeNull());
    fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
    await screen.findByRole("heading", { level: 2, name: /.+ and .+/ });
    expect(screen.queryByText(w.pinnedWords, { exact: false })).toBeNull();
    expect(screen.queryByRole("button", { name: /Pin connection|Unpin connection/ })).toBeNull();
  });

  it("not to a player who peeks, even if the API sent one", async () => {
    const api = pageWith(graph({ canEdit: false }));
    render(<Web api={api} path="/account/web/100.cmp1.web-player" />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.click(screen.getByRole("switch", { name: "Peek behind the DM screen" }));
    fireEvent.click(await screen.findByRole("button", { name: w.peekYes }));
    await waitFor(() => expect(screen.getByText(w.peeking)).toBeTruthy());
    expect(screen.queryByText(w.legendPinned)).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
    await screen.findByRole("heading", { level: 2, name: /.+ and .+/ });
    expect(screen.queryByText(w.pinnedWords, { exact: false })).toBeNull();
    expect(screen.queryByRole("button", { name: /Pin connection|Unpin connection/ })).toBeNull();
  });

  it("the pretend API sends a pin to a DM's Full View only", async () => {
    const api = mockApi("table");
    const dmFull = await api.webGraph!("100.cmp1", { center: null, hops: 6, view: "full" });
    const dmPlayers = await api.webGraph!("100.cmp1", { center: null, hops: 6, view: "players" });
    const peek = await api.webGraph!("100.cmp1.web-player", { center: null, hops: 6, view: "full" }).catch(() => null);
    const pinned = (g: WebGraph | null) => (g?.edges ?? []).some((e) => e.links.some((l) => l.pinned));
    expect(peek).not.toBeNull(); // (the player's peek is allowed here, so this really checks the peeker's web)
    expect(pinned(dmFull)).toBe(true);
    expect(pinned(dmPlayers)).toBe(false);
    expect(pinned(peek)).toBe(false);
  });
});

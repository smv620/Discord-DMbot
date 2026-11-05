/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Travels with" among the Story Graph's Add a connection choices (#3505): a DM sees the pick when the two names can
// be a companion and a player character (or the party), the companion is the connection's first name whichever way
// round the pair is stored, a pair that cannot be one gets a plain refusal before anything is sent, and the bot's own
// refusal is worded plainly. A player never sees the form.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, WebGraph, WebLinkChange } from "../src/account/api";
import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { companionEnd } from "../src/account/AddLink";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const graph = (kindA: string, kindB: string, canEdit = true): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Cerric", kind: kindA },
    { id: "n2", name: "Rook", kind: kindB },
  ],
  edges: [
    { a: "n1", b: "n2", label: "knows", more: 0, links: [{ id: "l1", words: "knows", secret: false, direction: "a-b" }] },
  ],
  truncated: false,
  maxHops: 6,
  canEdit,
});

function pageWith(g: WebGraph, changes: WebLinkChange[], fail?: ApiError): AccountApi {
  const api = mockApi("table");
  api.webGraph = async () => g;
  (api as unknown as { canEdit: boolean }).canEdit = g.canEdit;
  api.webSize = async () => ({ names: 2 });
  api.changeWebLink = async (_ref, change) => {
    changes.push(change);
    if (fail) throw fail;
    return g.edges[0]!;
  };
  return api;
}

const graphCanEdit = (api: AccountApi): boolean => (api as unknown as { canEdit?: boolean }).canEdit !== false;

async function openPanel(api: AccountApi): Promise<HTMLElement> {
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findByText(/names?, \d+ lines?/);
  fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
  const panel = (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
  // The form is its own file, fetched when a DM opens a line (the page-weight budget): wait for it, unless it is not there
  if (graphCanEdit(api)) await within(panel).findByRole("button", { name: w.addButton });
  return panel;
}

describe("which end is the companion", () => {
  it("is the character who is not a player character, with a player character or a group at the other end", () => {
    expect(companionEnd("npc", "player_character")).toBe("a");
    expect(companionEnd("player_character", "npc")).toBe("b");
    expect(companionEnd("character", "faction")).toBe("a");
    expect(companionEnd("faction", "npc")).toBe("b");
  });

  it("is nobody for any other pair, so no choice is offered", () => {
    expect(companionEnd("npc", "npc")).toBeNull();
    expect(companionEnd("player_character", "player_character")).toBeNull();
    expect(companionEnd("place", "player_character")).toBeNull();
    expect(companionEnd("npc", "place")).toBeNull();
  });
});

describe("the Travels with choice", () => {
  it("is offered to a DM for a companion and a player character, and fills in the words", async () => {
    const panel = await openPanel(pageWith(graph("npc", "player_character"), []));
    const pick = within(panel).getByRole("button", { name: w.travelsWithPick });
    fireEvent.click(pick);
    expect((within(panel).getByLabelText(w.addWords) as HTMLInputElement).value).toBe(w.travelsWithWords);
    expect(within(panel).getByText(w.travelsWithHint("Cerric", "Rook"))).toBeTruthy();
  });

  it("makes the companion the first name, whichever way round the pair is stored", async () => {
    const changes: WebLinkChange[] = [];
    // Rook (player character) is the pair's first name, Cerric the companion is the second
    const flipped = graph("player_character", "npc");
    const panel = await openPanel(pageWith(flipped, changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.travelsWithPick }));
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    await screen.findByText(w.added).catch(() => undefined);
    expect(changes).toEqual([{ action: "add", a: "n2", b: "n1", words: w.travelsWithWords, secret: false }]);
  });

  it("is not offered for a pair that cannot be a companion, and typed words get a plain refusal and send nothing", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph("npc", "npc"), changes));
    expect(within(panel).queryByRole("button", { name: w.travelsWithPick })).toBeNull();
    fireEvent.input(within(panel).getByLabelText(w.addWords), { target: { value: "travels with" } });
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    expect(await within(panel).findByText(w.travelsWithNo)).toBeTruthy();
    expect(changes).toEqual([]);
  });

  it("words the bot's refusal plainly when the rule says no", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph("npc", "faction"), changes, new ApiError("bad-target")));
    fireEvent.click(within(panel).getByRole("button", { name: w.travelsWithPick }));
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    expect(await within(panel).findByText(w.travelsWithRefused)).toBeTruthy();
    expect(changes).toHaveLength(1);
  });

  // Only the store's rule saying no gets the companion words; a failure of any other kind keeps its usual words (#3527 review).
  it.each([
    ["a network failure", new ApiError("network"), w.errors.network!],
    ["a busy server", new ApiError("still-waiting"), w.errors["still-waiting"]!],
    ["a gone name", new ApiError("not-found"), ""],
    ["a server fault", new ApiError("server"), w.errorFallback],
  ])("does not say the companion rule refused after %s", async (_name, failure, usual) => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph("npc", "player_character"), changes, failure));
    fireEvent.click(within(panel).getByRole("button", { name: w.travelsWithPick }));
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    await screen.findByRole("heading", { level: 2, name: /.+ and .+/ }).catch(() => undefined);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(w.travelsWithRefused)).toBeNull();
    if (usual) expect(screen.getAllByText(usual).length).toBeGreaterThan(0);
    expect(changes).toHaveLength(1);
  });

  it("does not say the companion rule refused when the person was signed out", async () => {
    const panel = await openPanel(pageWith(graph("npc", "player_character"), [], new ApiError("signed-out")));
    fireEvent.click(within(panel).getByRole("button", { name: w.travelsWithPick }));
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    await screen.findByText(w.signedOut).catch(() => undefined);
    expect(screen.queryByText(w.travelsWithRefused)).toBeNull();
  });

  it("sends other words as they were, in the order they were stored", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph("player_character", "npc"), changes));
    fireEvent.input(within(panel).getByLabelText(w.addWords), { target: { value: "works with" } });
    fireEvent.click(within(panel).getByRole("button", { name: w.addButton }));
    await screen.findByText(w.added).catch(() => undefined);
    expect(changes).toEqual([{ action: "add", a: "n1", b: "n2", words: "works with", secret: false }]);
  });

  it("is not shown to someone who cannot edit (a player)", async () => {
    const panel = await openPanel(pageWith(graph("npc", "player_character", false), []));
    expect(within(panel).queryByLabelText(w.addWords)).toBeNull();
    expect(within(panel).queryByRole("button", { name: w.travelsWithPick })).toBeNull();
  });
});

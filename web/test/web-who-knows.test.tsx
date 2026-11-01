/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Who knows this?" on a held connection (#2935 D4, #3214), behind WHO_KNOWS_LIVE: a DM in the full view says only
// the DM, some characters, or everyone at the table, and the bot applies it. A player, the Players View and See as…
// never see it, and a public connection shows nothing (it is already everyone's).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph, WebLink, WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/who-knows-switch", () => ({
  get WHO_KNOWS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

/** One connection between Mira and Aldric, with the given links. */
const graph = (links: WebLink[], canEdit = true): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Mira", kind: "npc" },
    { id: "n2", name: "Aldric", kind: "npc" },
  ],
  edges: [{ a: "n1", b: "n2", label: "trusts", more: links.length - 1, links }],
  truncated: false,
  maxHops: 2,
  canEdit,
  view: "full",
});
const held = (over: Partial<WebLink> = {}): WebLink => ({ id: "l1", words: "trusts", secret: true, direction: "a-b", ...over });

function pageWith(g: WebGraph, changes: WebLinkChange[] = []): AccountApi {
  const api = mockApi("table");
  api.webGraph = async (_ref, q) => (q.seeAs !== undefined ? { ...g, canEdit: false, seeAs: { viewer: q.seeAs, name: "The party" } } : g);
  api.webSize = async () => ({ names: 2 });
  api.changeWebLink = async (_ref, change) => {
    changes.push(change);
    // The change is kept on the link, as the bot would keep it, so the next read shows the new answer.
    const link = g.edges[0]!.links.find((l) => change.action !== "add" && l.id === change.id);
    if (link && change.action === "who") {
      link.who = change.who === "dm" ? "dm" : change.who === "everyone" ? "all" : "some";
      link.knownBy = change.who === "some" ? (change.characters ?? []).map((id) => ({ id, name: id })) : [];
    }
    return g.edges[0]!;
  };
  return api;
}

async function openPanel(api: AccountApi) {
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findByText(/names?, \d+ lines?/);
  fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
  return (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
}

describe("who knows this, on a held connection", () => {
  it("shows nothing while the switch is off", async () => {
    live.on = false;
    const panel = await openPanel(pageWith(graph([held()])));
    expect(within(panel).queryByText(w.whoKnowsHeading)).toBeNull();
  });

  it("shows the three answers on a hidden connection to a DM, the current one pressed", async () => {
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })])));
    expect(within(panel).getByText(w.whoKnowsHeading)).toBeTruthy();
    expect(within(panel).getByRole("button", { name: w.whoOnlyDm }).getAttribute("aria-pressed")).toBe("false");
    expect(within(panel).getByRole("button", { name: w.whoSome }).getAttribute("aria-pressed")).toBe("true");
    expect(within(panel).getByRole("button", { name: w.whoEveryone }).getAttribute("aria-pressed")).toBe("false");
  });

  it("shows nothing on a public connection, to a player, in Players View and in See as…", async () => {
    // A public connection: already everyone's.
    const publicPanel = await openPanel(pageWith(graph([held({ secret: false })])));
    expect(within(publicPanel).queryByText(w.whoKnowsHeading)).toBeNull();
    cleanup();

    // A player: a held connection is not drawn to them at all, so there is no picker to show.
    render(<Web api={pageWith(graph([held()], false))} path="/account/web/100.cmp1" />);
    await screen.findByText(/names?, \d+ lines?/);
    expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0);
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
    cleanup();

    // Players View: the DM sees what the players see, read-only, and a held connection is not drawn to them.
    const api = pageWith(graph([held()]));
    render(<Web api={api} path="/account/web/100.cmp1" />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(screen.getByLabelText(w.seeAsLabel)).toBeTruthy());
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0));
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
    cleanup();

    // See as…: read-only as one character knows it; the held connection is not drawn there either.
    const seeAs = pageWith(graph([held()]));
    render(<Web api={seeAs} path="/account/web/100.cmp1" />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.click(screen.getByLabelText(w.playersView));
    fireEvent.click(await screen.findByRole("button", { name: w.seeAsParty }));
    await screen.findByText(w.seeAsNow("The party"));
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0));
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
  });

  it("only the DM and everyone send at once, with no characters", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "dm" }]));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoEveryone }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "dm" }, { action: "who", id: "l1", who: "everyone" }]));
  });

  it("some needs a character and sends their ids", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held()]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSome }));
    const save = await within(panel).findByRole("button", { name: w.whoSave });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(within(panel).getByText(w.whoPickOne)).toBeTruthy();
    fireEvent.click(await within(panel).findByRole("checkbox", { name: "Cerric" }));
    fireEvent.click(within(panel).getByRole("checkbox", { name: "Mira" }));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSave }));
    await waitFor(() =>
      expect(changes).toEqual([{ action: "who", id: "l1", who: "some", characters: ["pc-cerric", "pc-mira"] }]),
    );
  });

  it("the state line names the characters, and \"and n more\" past five", async () => {
    const two = [
      { id: "a", name: "Mara" },
      { id: "b", name: "Tavin" },
    ];
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: two })])));
    expect(within(panel).getByText(w.whoStateSome("Mara and Tavin"))).toBeTruthy();
    expect(w.whoNames(["Mara", "Tavin", "Brann", "Lio", "Oda", "Pell"])).toBe("Mara, Tavin, Brann, Lio and 2 more");
  });

  it("the done words for each answer, and \"already like that\" when the answer is set", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await screen.findByText(w.whoDoneDm)).toBeTruthy();
    // Already the DM's: pressing it again sends nothing and says so.
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await within(panel).findByText(w.whoAlready)).toBeTruthy();
    expect(changes).toHaveLength(1);
  });
});

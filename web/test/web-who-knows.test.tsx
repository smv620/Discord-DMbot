/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Who knows this?" on a held connection (#2935 D4, #3214), behind WHO_KNOWS_LIVE: a DM in the full view says only
// the DM, some characters, or everyone at the table, and the bot applies it. A player, the Players View and See as…
// never see it, and a public connection shows nothing (it is already everyone's).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph, WebLink, WebLinkChange } from "../src/account/api";
import { ApiError } from "../src/account/api";
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
      link.who = change.who === "dm" ? "dm" : change.who === "everyone" ? "all" : change.who === "common" ? "common" : "some";
      link.knownBy = change.who === "some" ? (change.characters ?? []).map((id) => ({ id, name: id })) : [];
    }
    return g.edges[0]!;
  };
  return api;
}

async function openPanel(api: AccountApi) {
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findByText(/Showing \d+ names? and \d+ connections?/);
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
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0);
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
    cleanup();

    // Players View: the DM sees what the players see, read-only, and a held connection is not drawn to them.
    const api = pageWith(graph([held()]));
    render(<Web api={api} path="/account/web/100.cmp1" />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(screen.getByLabelText(w.seeAsLabel)).toBeTruthy());
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0));
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
    cleanup();

    // See as…: read-only as one character knows it; the held connection is not drawn there either.
    const seeAs = pageWith(graph([held()]));
    render(<Web api={seeAs} path="/account/web/100.cmp1" />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click(screen.getByLabelText(w.playersView));
    fireEvent.click(await screen.findByRole("button", { name: w.seeAsParty }));
    await screen.findByText(w.seeAsNow("The party"));
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /^See the connections between/ })).toHaveLength(0));
    expect(screen.queryByText(w.whoKnowsHeading)).toBeNull();
  });

  it("a common connection shows the common line and the common button pressed", async () => {
    const panel = await openPanel(pageWith(graph([held({ who: "common" })])));
    expect(within(panel).getByText(w.whoStateCommon)).toBeTruthy();
    expect(within(panel).getByRole("button", { name: w.whoCommon }).getAttribute("aria-pressed")).toBe("true");
    expect(within(panel).getByRole("button", { name: w.whoEveryone }).getAttribute("aria-pressed")).toBe("false");
  });

  it("pressing Common knowledge sends who common and says it is done", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held()]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoCommon }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "common" }]));
    expect(await screen.findByText(w.whoDoneCommon)).toBeTruthy();
  });

  it("pressing it again on a common connection says it is already so and sends nothing", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "common" })]), changes));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoCommon }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoCommon }));
    expect(await within(panel).findByText(w.whoAlready)).toBeTruthy();
    expect(changes).toHaveLength(0);
  });

  it("a refusal says to show the names to the players first", async () => {
    const api = pageWith(graph([held()]));
    api.changeWebLink = async () => {
      throw new ApiError("cannot-show");
    };
    const panel = await openPanel(api);
    fireEvent.click(within(panel).getByRole("button", { name: w.whoCommon }));
    expect(await within(panel).findByText(w.whoCannotShow)).toBeTruthy();
  });

  it("only the DM, some and everyone still send what they did", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "common" })]), changes));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoOnlyDm }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "dm" }]));
    expect(await screen.findByText(`${w.whoNoLongerCommon} ${w.whoDoneDm}`)).toBeTruthy();
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoEveryone }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoEveryone }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "dm" }, { action: "who", id: "l1", who: "everyone" }]));
    expect(await screen.findByText(w.whoDoneAll)).toBeTruthy();
  });

  it("taking back common knowledge with only the DM says so first", async () => {
    const panel = await openPanel(pageWith(graph([held({ who: "common" })])));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoOnlyDm }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await screen.findByText(`${w.whoNoLongerCommon} ${w.whoDoneDm}`)).toBeTruthy();
  });

  it("taking back common knowledge with everyone says so first", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "common" })]), changes));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoEveryone }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoEveryone }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "everyone" }]));
    expect(await screen.findByText(`${w.whoNoLongerCommon} ${w.whoDoneAll}`)).toBeTruthy();
  });

  it("taking back common knowledge with some characters says so first", async () => {
    const panel = await openPanel(pageWith(graph([held({ who: "common" })])));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoSome }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSome }));
    fireEvent.click(await within(panel).findByRole("checkbox", { name: "Cerric" }));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSave }));
    expect(await screen.findByText(`${w.whoNoLongerCommon} ${w.whoDoneSome("Cerric")}`)).toBeTruthy();
  });

  it("an answer on a connection that was not common has no such line", async () => {
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })])));
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoOnlyDm }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await screen.findByText(w.whoDoneDm)).toBeTruthy();
    expect(screen.queryByText(new RegExp(w.whoNoLongerCommon))).toBeNull();
  });

  it("only the DM and everyone send at once, with no characters", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    await waitFor(() => expect(changes).toEqual([{ action: "who", id: "l1", who: "dm" }]));
    // The bot answers before the next press: the controls are held until then.
    expect(await screen.findByText(w.whoDoneDm)).toBeTruthy();
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
    await waitFor(() => expect(changes).toHaveLength(1));
    // The save also says which characters the picker's list showed (#4970, core #4976): the ticked two are among them.
    expect(changes[0]).toMatchObject({ action: "who", id: "l1", who: "some", characters: ["pc-cerric", "pc-mira"] });
    expect(changes[0]).toHaveProperty("shown");
    expect((changes[0] as { shown: string[] }).shown).toEqual(expect.arrayContaining(["pc-cerric", "pc-mira"]));
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

  it("the done words for the DM and for everyone, and \"already like that\" when the answer is set", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: [{ id: "pc-cerric", name: "Cerric" }] })]), changes));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await screen.findByText(w.whoDoneDm)).toBeTruthy();
    // Already the DM's: pressing it again sends nothing and says so.
    await waitFor(() => expect((within(panel).getByRole("button", { name: w.whoOnlyDm }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoOnlyDm }));
    expect(await within(panel).findByText(w.whoAlready)).toBeTruthy();
    expect(changes).toHaveLength(1);
  });

  it("a rendered state line names six characters as four and how many more", async () => {
    const six = ["Mara", "Tavin", "Brann", "Lio", "Oda", "Pell"].map((name, i) => ({ id: `c${i}`, name }));
    const panel = await openPanel(pageWith(graph([held({ who: "some", knownBy: six })])));
    expect(within(panel).getByText(w.whoStateSome("Mara, Tavin, Brann, Lio and 2 more"))).toBeTruthy();
  });
});

// The "Who knows this?" character list uses the same order and leaver's note as See as… (#4888).
describe("who knows this: the character list is in the API's order, with a leaver's note (#4888)", () => {
  it("the character list lists in the order it is given and shows a leaver's note", async () => {
    const panel = await openPanel(pageWith(graph([held()])));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSome }));
    await within(panel).findByRole("checkbox", { name: "Tavin (Retired)" });
    const labels = within(within(panel).getByRole("list", { name: w.whoSomeLabel }))
      .getAllByRole("checkbox")
      .map((c) => c.closest("label")?.textContent);
    expect(labels.slice(0, 4)).toEqual(["Cerric", "Mira", "Tavin (Retired)", "Guard 1"]);
  });

  it("a name with no note shows no brackets", async () => {
    const panel = await openPanel(pageWith(graph([held()])));
    fireEvent.click(within(panel).getByRole("button", { name: w.whoSome }));
    const mira = await within(panel).findByRole("checkbox", { name: "Mira" });
    expect(mira.closest("label")?.textContent).toBe("Mira");
  });
});


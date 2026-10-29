/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph page (#2324): re-centring, steps out, the big-web question, the connection panel
// and its edits, and what a server manager who isn't a DM never sees.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { BIG_WEB, webPage as w } from "../src/content/web-words";
import type { WebGraph } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web, { webRefFromPath } from "../src/account/Web";
import { clampHops, radialLayout, zoomAbout } from "../src/account/webLayout";

afterEach(cleanup);

const at = (suffix = ""): string => `/account/web/100.cmp1${suffix}`;

async function open(suffix = "") {
  const api = mockApi("table");
  render(<Web api={api} path={at(suffix)} />);
  await screen.findByText(/names?, \d+ lines?/);
  return api;
}

describe("the address", () => {
  it("reads the campaign from the path", () => {
    expect(webRefFromPath("/account/web/100.cmp1")).toBe("100.cmp1");
    expect(webRefFromPath("/account/web/")).toBeNull();
    expect(webRefFromPath("/account/web/%E0%A4%A")).toBeNull();
  });
});

describe("the layout", () => {
  it("puts the middle name at the centre and the rest on rings by steps", () => {
    const nodes = ["a", "b", "c", "d"].map((id) => ({ id, name: id, kind: "npc" }));
    const edge = (a: string, b: string) => ({ a, b, label: "x", more: 0, links: [] });
    const out = radialLayout(nodes, [edge("a", "b"), edge("b", "c")], "a");
    expect(out.points.get("a")).toMatchObject({ x: 0, y: 0, ring: 0 });
    expect(out.points.get("b")?.ring).toBe(1);
    expect(out.points.get("c")?.ring).toBe(2);
    // d is connected to nothing in view; it still has a place.
    expect(out.points.get("d")).toBeDefined();
  });
  it("keeps the steps between 1 and the most the API allows", () => {
    expect(clampHops(0, 6)).toBe(1);
    expect(clampHops(9, 6)).toBe(6);
    expect(clampHops(3, 6)).toBe(3);
  });
});

describe("reading the web", () => {
  it("draws the names and lists them too", async () => {
    await open();
    // The drawing is hidden from screen readers; the lists carry the same names.
    expect(document.querySelector("svg[aria-label^='A map of']")).toBeTruthy();
    expect(screen.getByText("The same web, as a list")).toBeTruthy();
  });

  it("puts another name in the middle", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    const button = screen.getAllByRole("button", { name: /^Put .* in the middle$/ })[0] as HTMLElement;
    fireEvent.click(button);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("webGraph")).length).toBeGreaterThan(1));
    expect(api.calls.filter((c) => c.startsWith("webGraph")).at(-1)).toMatch(/center=\S+:hops=2/);
  });

  it("asks for more steps out", async () => {
    const api = await open();
    fireEvent.input(screen.getByLabelText("Steps out from the middle"), { target: { value: "3" } });
    await waitFor(() => expect(api.calls.some((c) => c.endsWith("hops=3"))).toBe(true));
  });

  it("asks before drawing a very big web, and draws it on a yes", async () => {
    const api = await open(".web-big");
    fireEvent.input(screen.getByLabelText("Steps out from the middle"), { target: { value: "6" } });
    const ask = await screen.findByText(/This will draw \d+ names and may be slow/);
    const n = Number(/draw (\d+)/.exec(ask.textContent ?? "")?.[1]);
    expect(n).toBeGreaterThan(BIG_WEB);
    // Nothing was drawn for the question: only the size was asked.
    expect(api.calls.filter((c) => c.startsWith("webGraph") && c.endsWith("hops=6"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Show it anyway" }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webGraph") && c.endsWith("hops=6"))).toBe(true));
  });
});

describe("the connections", () => {
  async function openLine() {
    const api = await open();
    fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
    const panel = (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
    return { api, panel };
  }

  it("lets a DM change the words, and says so", async () => {
    const { api, panel } = await openLine();
    const box = within(panel).getByLabelText("Words for connection 1") as HTMLInputElement;
    fireEvent.input(box, { target: { value: "betrayed" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save words for connection 1" }));
    await waitFor(() => expect(api.calls.some((c) => c.includes('"action":"words"') && c.includes("betrayed"))).toBe(true));
    expect(await screen.findByText("Saved.")).toBeTruthy();
  });

  it("asks before removing a connection", async () => {
    const { api, panel } = await openLine();
    fireEvent.click(within(panel).getByRole("button", { name: "Remove connection 1" }));
    expect(api.calls.some((c) => c.includes('"action":"remove"'))).toBe(false);
    fireEvent.click(within(panel).getByRole("button", { name: "Yes, remove it" }));
    await waitFor(() => expect(api.calls.some((c) => c.includes('"action":"remove"') && c.includes('"confirm":true'))).toBe(true));
    expect(await screen.findByText("Removed.")).toBeTruthy();
  });

  it("tells a DM when the words are empty", async () => {
    const { panel } = await openLine();
    fireEvent.click(within(panel).getByRole("button", { name: "Add" }));
    expect(await within(panel).findByText(/Type some words/)).toBeTruthy();
  });
});

describe("who sees what", () => {
  it("shows a server manager no secrets and no way to change anything", async () => {
    await open(".web-manager");
    expect(screen.getByText("Only this campaign's DM can change names and connections.")).toBeTruthy();
    expect(screen.queryByText(/🤫/)).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
    await screen.findByRole("heading", { level: 2, name: /.+ and .+/ });
    expect(screen.queryByRole("button", { name: /Remove connection/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  });

  it("says not found to someone who may not see it", async () => {
    render(<Web api={mockApi("table")} path={at(".web-none")} />);
    expect(await screen.findByText(/We couldn't find that web/)).toBeTruthy();
  });
});

describe("zooming", () => {
  it("stays between the least and most, and keeps the point under the finger still", () => {
    expect(zoomAbout({ x: 0, y: 0, k: 1 }, { x: 0, y: 0 }, 100).k).toBe(4);
    expect(zoomAbout({ x: 0, y: 0, k: 1 }, { x: 0, y: 0 }, 0.001).k).toBe(0.25);
    const v = zoomAbout({ x: 10, y: 20, k: 1 }, { x: 100, y: 100 }, 2);
    // A drawing point p lands at x + p * k; the one that was at 100 still is.
    const p = (100 - 10) / 1;
    expect(v.x + p * v.k).toBeCloseTo(100);
  });
});

describe("a very big web on first open", () => {
  it("asks first, and does not call the DM read-only or the web empty", async () => {
    const api = mockApi("table");
    const graph = (): WebGraph => ({ center: "n1", nodes: [{ id: "n1", name: "Mira", kind: "npc" }], edges: [], truncated: false, maxHops: 6, canEdit: true });
    api.webSize = async () => ({ names: BIG_WEB + 50 });
    api.webGraph = async () => graph();
    render(<Web api={api} path={at()} />);
    expect(await screen.findByText(/This will draw 350 names/)).toBeTruthy();
    expect(screen.queryByText(/Only this campaign's DM/)).toBeNull();
    expect(screen.queryByText("No names are connected here yet.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show it anyway" }));
    await screen.findByText("1 name, 0 lines");
  });
});

describe("secrets for someone who is not a DM", () => {
  it("never shows a secret connection's words, even if the API sent them", async () => {
    const api = mockApi("table");
    api.webSize = async () => ({ names: 2 });
    api.webGraph = async () => ({
      center: "a",
      nodes: [
        { id: "a", name: "Mira", kind: "npc" },
        { id: "b", name: "Garrick", kind: "npc" },
      ],
      edges: [
        {
          a: "a",
          b: "b",
          label: "is secretly the heir of",
          more: 1,
          links: [
            { id: "l1", words: "serves", secret: false, direction: "a-b" },
            { id: "l2", words: "is secretly the heir of", secret: true, direction: "a-b" },
          ],
        },
      ],
      truncated: false,
      maxHops: 3,
      canEdit: false,
    });
    render(<Web api={api} path={at()} />);
    await screen.findByText("2 names, 1 line");
    expect(document.body.textContent).not.toMatch(/heir/);
    expect(document.body.textContent).toMatch(/serves/);
  });
});

describe("the steps box", () => {
  it("takes a typed number, and a number out of range asks for nothing", async () => {
    const api = await open();
    const box = screen.getByLabelText("Steps out from the middle") as HTMLInputElement;
    expect(box.type).toBe("number");
    // The campaign's nav reads the campaign too (#2461); let that settle so it is not counted below.
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("campaignPage"))).toBe(true));
    const before = api.calls.length;
    fireEvent.input(box, { target: { value: "99" } });
    fireEvent.input(box, { target: { value: "" } });
    expect(api.calls.length).toBe(before);
    fireEvent.blur(box);
    expect(box.value).toBe("2"); // the steps in use come back
    fireEvent.input(box, { target: { value: "4" } });
    await waitFor(() => expect(api.calls.some((c) => c.endsWith("hops=4"))).toBe(true));
  });
});

describe("saying no to a very big first draw", () => {
  it("shows the nearest names instead of a blank page", async () => {
    const api = mockApi("table");
    const calls: number[] = [];
    api.webSize = async (_r, q) => ({ names: q.hops > 1 ? BIG_WEB + 1 : 3 });
    const real = api.webGraph!;
    api.webGraph = async (r, q) => {
      calls.push(q.hops);
      return real(r, q);
    };
    render(<Web api={api} path={at()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    await screen.findByText(/names?, \d+ lines?/);
    expect(calls).toEqual([1]);
    expect(screen.getByLabelText("Steps out from the middle")).toBeTruthy();
  });
});

describe("changing a name", () => {
  it("lets a DM change spelling and kind, and shows the change", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    fireEvent.click(screen.getAllByRole("button", { name: /^Change / })[0] as HTMLElement);
    const group = screen.getByRole("group", { name: /^About / });
    fireEvent.input(within(group).getByLabelText("Spelling"), { target: { value: "Zed the Bold" } });
    fireEvent.click(within(group).getByRole("button", { name: "Save spelling" }));
    await waitFor(() => expect(api.calls.some((c) => c.includes('"action":"spell"') && c.includes("Zed the Bold"))).toBe(true));
    expect(await screen.findAllByText(/Zed the Bold/)).not.toHaveLength(0);
    fireEvent.change(within(screen.getByRole("group", { name: /^About / })).getByLabelText("What it is"), { target: { value: "place" } });
    fireEvent.click(within(screen.getByRole("group", { name: /^About / })).getByRole("button", { name: "Save what it is" }));
    await waitFor(() => expect(api.calls.some((c) => c.includes('"action":"kind"') && c.includes("place"))).toBe(true));
  });

  it("shows a server manager no way to change a name", async () => {
    await open(".web-manager");
    expect(screen.queryByRole("button", { name: /^Change / })).toBeNull();
  });
});

describe("how many more connections a line has", () => {
  const stub = (canEdit: boolean) => {
    const api = mockApi("table");
    api.webSize = async () => ({ names: 3 });
    api.webGraph = async () => ({
      center: "a",
      nodes: [
        { id: "a", name: "Mira", kind: "npc" },
        { id: "b", name: "Garrick", kind: "npc" },
        { id: "c", name: "Hidden Heir", kind: "npc", secret: true },
      ],
      edges: [
        {
          a: "a",
          b: "b",
          label: "serves",
          more: 3,
          links: [
            { id: "l1", words: "serves", secret: false, direction: "a-b" },
            { id: "l2", words: "owes", secret: false, direction: "a-b" },
            { id: "l3", words: "fears", secret: true, direction: "a-b" },
            { id: "l4", words: "hunts", secret: true, direction: "a-b" },
          ],
        },
        { a: "a", b: "c", label: "guards", more: 0, links: [{ id: "l5", words: "guards", secret: false, direction: "a-b" }] },
      ],
      truncated: false,
      maxHops: 3,
      canEdit,
    });
    return api;
  };

  it("says (+3) on the map, and gives each connection its own line in the list, for a DM", async () => {
    render(<Web api={stub(true)} path={at()} />);
    await screen.findByText("3 names, 2 lines");
    for (const w of ["Mira serves Garrick", "Mira owes Garrick", "Mira fears Garrick", "Mira hunts Garrick"]) {
      expect(within(document.getElementById("web-list")!).getByText(new RegExp(w))).toBeTruthy();
    }
    expect(document.body.textContent).not.toContain("↔");
    // At rest a line with several connections says how many; hovering a name shows that name's lines in words.
    await waitFor(() => expect(document.querySelector(".web-edge-text")?.textContent ?? "").toContain("4 connections"));
    const mira = [...document.querySelectorAll(".web-node")].find((n) => n.textContent?.includes("Mira"))!;
    fireEvent.pointerEnter(mira);
    await waitFor(() => expect(document.querySelector(".web-edge-text")?.textContent ?? "").toContain("(+3)"));
  });

  it("recounts for someone who is not a DM, and drops a secret name", async () => {
    render(<Web api={stub(false)} path={at()} />);
    await screen.findByText("2 names, 1 line");
    expect(within(document.getElementById("web-list")!).getByText(/Mira owes Garrick/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Hidden Heir/);
    expect(document.body.textContent).not.toMatch(/fears|hunts/);
  });
});

describe("the Open its page link", () => {
  it("a name marked page true has the Open its page link, pointing at the NPC page address", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    const links = screen.getAllByRole("link", { name: (label: string) => label.startsWith(w.openItsPage) });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]!.getAttribute("href")).toMatch(/^\/account\/npc\/100\.cmp1\/[^/]+$/);
  });

  it("a name without page, or with page false, has no link", async () => {
    const api = mockApi("table");
    const real = api.webGraph!.bind(api);
    api.webGraph = async (...args) => {
      const got = await real(...args);
      return { ...got, nodes: got.nodes.map((n) => ({ ...n, page: false })) };
    };
    const overview = api.webOverview!.bind(api);
    api.webOverview = async (...args) => {
      const got = await overview(...args);
      return {
        ...got,
        tray: got.tray.map((n) => ({ ...n, page: false })),
        groups: got.groups.map((g) => ({ ...g, page: false })),
      };
    };
    render(<Web api={api} path={at(".web-lonely")} />);
    await screen.findByText(/names?, \d+ lines?/);
    await screen.findByRole("region", { name: new RegExp(w.trayHeading) });
    expect(screen.queryAllByRole("link", { name: (label: string) => label.startsWith(w.openItsPage) })).toHaveLength(0);
    expect(screen.queryByText(w.openItsPage)).toBeNull();
  });

  it("the link's words appear once per link and nowhere else on the page", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    const shown = (document.body.textContent ?? "").split(w.openItsPage).length - 1;
    expect(shown).toBe(screen.getAllByRole("link", { name: (label: string) => label.startsWith(w.openItsPage) }).length);
  });
});

describe("every connection between two names, in both directions (#2934)", () => {
  it("groups them as Both ways, then one column for each way", async () => {
    const api = mockApi("table");
    api.webSize = async () => ({ names: 2 });
    api.webGraph = async () => ({
      center: "a",
      nodes: [
        { id: "a", name: "Mira", kind: "npc" },
        { id: "b", name: "Garrick", kind: "npc" },
      ],
      edges: [
        {
          a: "a",
          b: "b",
          label: "serves",
          more: 2,
          links: [
            { id: "l1", words: "married to", secret: false, direction: "both" },
            { id: "l2", words: "serves", secret: false, direction: "a-b" },
            { id: "l3", words: "owes", secret: false, direction: "b-a" },
          ],
        },
      ],
      truncated: false,
      maxHops: 3,
      canEdit: true,
    });
    render(<Web api={api} path={at()} />);
    await screen.findByText("2 names, 1 line");
    fireEvent.click((await screen.findAllByRole("button", { name: /^See the connections between/ }))[0] as HTMLElement);
    const panel = (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
    const both = within(panel).getByRole("region", { name: "Both ways" });
    expect(within(both).getByText(/married to/)).toBeTruthy();
    const forward = within(panel).getByRole("region", { name: "Mira → Garrick" });
    expect(within(forward).getByText(/Mira serves Garrick/)).toBeTruthy();
    expect(within(forward).queryByText(/owes/)).toBeNull();
    const back = within(panel).getByRole("region", { name: "Garrick → Mira" });
    expect(within(back).getByText(/Garrick owes Mira/)).toBeTruthy();
    // Each connection keeps its own number, so its words box is still found by "connection N".
    expect(within(panel).getByLabelText("Words for connection 3")).toBeTruthy();
  });
});

describe("the kind of each connection in the line panel (#2934)", () => {
  it("shows the kind's word and a dot above each connection", async () => {
    const api = mockApi("table");
    api.webSize = async () => ({ names: 2 });
    api.webGraph = async () => ({
      center: "a",
      nodes: [
        { id: "a", name: "Mira", kind: "npc" },
        { id: "b", name: "Garrick", kind: "npc" },
      ],
      edges: [{ a: "a", b: "b", label: "serves", more: 0, links: [{ id: "l1", words: "serves", secret: false, direction: "a-b", kind: "work" }] }],
      truncated: false,
      maxHops: 3,
      canEdit: true,
    });
    render(<Web api={api} path={at()} />);
    await screen.findByText("2 names, 1 line");
    fireEvent.click((await screen.findAllByRole("button", { name: /^See the connections between/ }))[0] as HTMLElement);
    const panel = (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
    expect(panel.querySelector(".web-kindmark")?.textContent?.trim()).toBe("Work");
    expect(panel.querySelector(".web-dot")).not.toBeNull();
  });
});


describe("the list at the bottom (#2984)", () => {
  const toggles = () => [...document.querySelectorAll<HTMLButtonElement>(".web-name-toggle")];

  it("sits after the map, closed, and the map comes right after the search", async () => {
    await open();
    const list = document.getElementById("web-list") as HTMLDetailsElement;
    expect(list.tagName).toBe("DETAILS");
    expect(list.open).toBe(false);
    const map = document.querySelector(".web-map")!;
    expect(map.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The two hint lines make way: they come after the map, not before it.
    const lead = screen.getByText(w.lead);
    expect(map.compareDocumentPosition(lead) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows only the names until one is opened, then its buttons", async () => {
    await open();
    expect(toggles().length).toBeGreaterThan(1);
    expect(screen.queryAllByRole("button", { name: /^Put .* in the middle$/ })).toHaveLength(0);
    expect(screen.queryAllByRole("button", { name: /^Change / })).toHaveLength(0);
    const first = toggles().find((t) => !t.textContent?.includes(w.inMiddle))!;
    expect(first.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(first);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByRole("button", { name: /^Put .* in the middle$/ })).toHaveLength(1);
    fireEvent.click(first);
    expect(screen.queryAllByRole("button", { name: /^Put .* in the middle$/ })).toHaveLength(0);
  });

  it("opens every name with Expand all and closes them with Collapse all", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    expect(toggles().every((t) => t.getAttribute("aria-expanded") === "true")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: w.collapseAll }));
    expect(toggles().every((t) => t.getAttribute("aria-expanded") === "false")).toBe(true);
  });

  it("opens the list when the skip link is used", async () => {
    await open();
    fireEvent.click(screen.getByRole("link", { name: w.skipMap }));
    expect((document.getElementById("web-list") as HTMLDetailsElement).open).toBe(true);
  });
});

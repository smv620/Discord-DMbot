/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The feeling picker on a "knows (…)" connection in the line panel (#3607, behind KNOWS_FEELING_LIVE): the current
// feeling is chosen, saving sends it as a feeling (never as the catch-all words), "Why?" shows where it came from and the
// DM-only quote, and a player's view shows neither the picker nor the quote.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph, WebLink, WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { FEELINGS, feelingOf } from "../src/content/knows-feeling-words";
import { feelingWords as w } from "../src/content/knows-feeling-text";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/knows-feeling-switch", () => ({
  get KNOWS_FEELING_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const knows = (over: Partial<WebLink> = {}): WebLink => ({
  id: "l1",
  words: "knows (wary)",
  secret: false,
  direction: "a-b",
  source: { type: "upload", label: "Notes on the Vale", line: 67 },
  quote: "They argued over the toll.",
  ...over,
});
const graph = (links: WebLink[], canEdit = true): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Mira", kind: "npc" },
    { id: "n2", name: "Aldric", kind: "npc" },
  ],
  edges: [{ a: "n1", b: "n2", label: "knows (wary)", more: links.length - 1, links }],
  truncated: false,
  maxHops: 2,
  canEdit,
  view: "full",
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
  await screen.findByText(/names?, \d+ lines?/);
  fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
  return (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
}

describe("reading the feeling", () => {
  it("reads one of the nine words out of a knows connection, and nothing else", () => {
    expect(FEELINGS).toHaveLength(9);
    expect(feelingOf("knows (hostile)")).toBe("hostile");
    expect(feelingOf("knows (Sworn Enemy)")).toBe("sworn enemy");
    expect(feelingOf("knows (heated)")).toBeNull();
    expect(feelingOf("trusts")).toBeNull();
  });
});

describe("the picker", () => {
  it("shows nothing while the switch is off: the words box stays", async () => {
    live.on = false;
    const panel = await openPanel(pageWith(graph([knows()])));
    expect(within(panel).queryByLabelText(w.label(1))).toBeNull();
    expect(within(panel).getByLabelText(/^Words for connection 1/)).toBeTruthy();
  });

  it("shows the current feeling chosen, instead of the free words", async () => {
    const panel = await openPanel(pageWith(graph([knows()])));
    const select = (await within(panel).findByLabelText(w.label(1))) as HTMLSelectElement;
    expect(select.value).toBe("wary");
    expect(within(panel).queryByLabelText(/^Words for connection 1/)).toBeNull();
  });

  it("saves the picked feeling as a feeling, never as the words", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([knows()]), changes));
    const select = (await within(panel).findByLabelText(w.label(1))) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "hostile" } });
    fireEvent.click(within(panel).getByRole("button", { name: w.saveFor(1) }));
    await waitFor(() => expect(changes).toHaveLength(1));
    expect(changes[0]).toEqual({ action: "feeling", id: "l1", feeling: "hostile" });
  });

  it("sends nothing when the feeling is the one it already is", async () => {
    const changes: WebLinkChange[] = [];
    const panel = await openPanel(pageWith(graph([knows()]), changes));
    await within(panel).findByLabelText(w.label(1));
    fireEvent.click(within(panel).getByRole("button", { name: w.saveFor(1) }));
    expect(changes).toHaveLength(0);
  });

  it("leaves a connection that is not a knows with the words box", async () => {
    const panel = await openPanel(pageWith(graph([knows({ words: "trusts", quote: undefined })])));
    expect(within(panel).getByLabelText(/^Words for connection 1/)).toBeTruthy();
    expect(within(panel).queryByLabelText(w.label(1))).toBeNull();
  });
});

describe("Why?", () => {
  it("shows where it came from and the DM-only quote", async () => {
    const panel = await openPanel(pageWith(graph([knows()])));
    expect(await within(panel).findByText(w.why("wary"))).toBeTruthy();
    expect(within(panel).getByText(/line 67/)).toBeTruthy();
    expect(within(panel).getByText(/They argued over the toll/)).toBeTruthy();
  });

  it("says plainly when nothing is recorded", async () => {
    const panel = await openPanel(pageWith(graph([knows({ source: { type: "unknown" }, quote: null })])));
    await within(panel).findByText(w.why("wary"));
    expect(within(panel).getByText(new RegExp(w.noSource))).toBeTruthy();
  });
});

describe("a peeker's view", () => {
  it("shows neither the picker nor the quote, even when the API sent it", async () => {
    const api = pageWith(graph([knows()], false));
    const panel = await openPanel(api);
    expect(within(panel).queryByText(/They argued over the toll/)).toBeNull();
  });
});

describe("a player's view", () => {
  it("shows neither the picker nor the quote", async () => {
    const panel = await openPanel(pageWith(graph([knows()], false)));
    expect(within(panel).queryByLabelText(w.label(1))).toBeNull();
    expect(within(panel).queryByText(/They argued over the toll/)).toBeNull();
    expect(screen.queryByText(w.why("wary"))).toBeNull();
  });
});

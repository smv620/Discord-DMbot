/** @jsxImportSource preact */
// @vitest-environment happy-dom
// How the two people of a `knows` connection know each other (#3882, behind KNOWS_HOW_LIVE; core #3889): the words "has met" /
// "has heard of" / plain "knows" are read, the DM's control sends `knows-how` and the feeling stays, a player gets no control,
// and with the switch off nothing changes.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph, WebLink, WebLinkChange } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { feelingOf } from "../src/content/knows-feeling-words";
import { knowsHowOf, knowsHowWords as w, withKnowsHow } from "../src/content/knows-how-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/knows-how-switch", () => ({
  get KNOWS_HOW_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

describe("reading how", () => {
  it("reads the three leads, with or without a feeling, and nothing else", () => {
    expect(knowsHowOf("has met")).toBe("met");
    expect(knowsHowOf("has met (wary)")).toBe("met");
    expect(knowsHowOf("has heard of")).toBe("aware");
    expect(knowsHowOf("has heard of (trusts)")).toBe("aware");
    expect(knowsHowOf("knows")).toBe("");
    expect(knowsHowOf("knows (close)")).toBe("");
    for (const other of ["sister of", "has as a member", "knows of", "is aware of", "knowsome", ""]) expect(knowsHowOf(other), other).toBeNull();
  });
  it("changes only the lead and keeps the feeling", () => {
    expect(withKnowsHow("knows (wary)", "met")).toBe("has met (wary)");
    expect(withKnowsHow("has met (wary)", "aware")).toBe("has heard of (wary)");
    expect(withKnowsHow("has heard of", "")).toBe("knows");
  });
  it("still reads the feeling out of a connection that says how", () => {
    expect(feelingOf("has met (wary)")).toBe("wary");
    expect(feelingOf("has heard of (trusts)")).toBe("trusts");
  });
});

const link = (over: Partial<WebLink> = {}): WebLink => ({ id: "l1", words: "has met (wary)", secret: false, direction: "a-b", ...over });
const graph = (links: WebLink[], canEdit = true): WebGraph => ({
  center: "n1",
  nodes: [
    { id: "n1", name: "Mira", kind: "npc" },
    { id: "n2", name: "Aldric", kind: "npc" },
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

describe("the DM's control", () => {
  it("shows the current way and sends knows-how on Save, as the DM's word", async () => {
    const changes: WebLinkChange[] = [];
    await openPanel(pageWith(graph([link()]), changes));
    const select = (await screen.findByLabelText(w.label(1))) as HTMLSelectElement;
    expect(select.value).toBe("met");
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([w.options.met, w.options.aware, w.options[""]]);
    fireEvent.change(select, { target: { value: "aware" } });
    fireEvent.click(screen.getByRole("button", { name: w.saveFor(1) }));
    await waitFor(() => expect(changes).toEqual([{ action: "knows-how", id: "l1", how: "aware" }]));
  });
  it("shows the control on a plain knows and on has heard of too, set to what it says", async () => {
    await openPanel(pageWith(graph([link({ words: "knows" })])));
    expect(((await screen.findByLabelText(w.label(1))) as HTMLSelectElement).value).toBe("");
  });
  it("has no control on another kind of connection", async () => {
    await openPanel(pageWith(graph([link({ words: "sister of" })])));
    expect(screen.queryByLabelText(w.label(1))).toBeNull();
  });
  it("gives a player no control", async () => {
    await openPanel(pageWith(graph([link()], false)));
    expect(screen.queryByLabelText(w.label(1))).toBeNull();
  });
  it("shows today's panel while the switch is off", async () => {
    live.on = false;
    await openPanel(pageWith(graph([link()])));
    expect(screen.queryByLabelText(w.label(1))).toBeNull();
  });
});

describe("the pretend API", () => {
  it("changes how and keeps the feeling, and refuses a connection that is not a knows", async () => {
    const api = mockApi("table");
    const edge = (await api.changeWebLink!("100.cmp1.knowshow", { action: "knows-how", id: "kh1", how: "aware" })) as { links: { id: string; words: string }[] };
    expect(edge.links.find((l) => l.id === "kh1")?.words).toBe("has heard of (wary)");
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Story Graph node ids are opaque (#3085): an "alias:<32 hex>" node stands for a secret other name and has no NPC page.
// Nothing links to a page for it, and choosing it as the middle sends the id through whole.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { isAliasNode, npcPageHref } from "../src/account/webNodes";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const ALIAS = `alias:${"a1".repeat(16)}`;
const REAL = "b2".repeat(16);

describe("npcPageHref", () => {
  it("gives a page for an entry and none for an alias node", () => {
    expect(npcPageHref("100.cmp1", REAL)).toBe(`/account/npc/100.cmp1/${REAL}`);
    expect(npcPageHref("100.cmp1", ALIAS)).toBeNull();
    expect(isAliasNode(ALIAS)).toBe(true);
    expect(isAliasNode(REAL)).toBe(false);
  });
});

describe("the overview with an alias node", () => {
  it("shows no page link for it even if the API says it has a page, and re-centres on the whole id", async () => {
    const api = mockApi("table");
    const real = api.webOverview!.bind(api);
    api.webOverview = async (ref, query) => {
      const got = await real(ref, query);
      const first = got.groups[0]!;
      return {
        ...got,
        groups: [
          { ...first, page: true },
          { ...first, center: ALIAS, centerName: "The Veiled Stranger", page: true },
        ],
      };
    };
    const centres: string[] = [];
    const graph = api.webGraph!.bind(api);
    api.webGraph = async (ref, query) => {
      if (query.center) centres.push(query.center);
      return graph(ref, query);
    };
    render(<Web api={api} path="/account/web/100.cmp1.web-lonely" />);
    const button = await screen.findByRole("button", { name: new RegExp("The Veiled Stranger") });
    const group = button.closest("li")!;
    expect(document.querySelector('a[href^="/account/npc/"]')).not.toBeNull(); // (a real group still has its link)
    expect(group.querySelector("a")).toBeNull(); // (no NPC page link for the alias)
    expect(document.body.innerHTML).not.toContain(`/account/npc/100.cmp1/${encodeURIComponent(ALIAS)}`);
    expect(document.body.innerHTML).not.toContain(`/account/npc/100.cmp1/${ALIAS}`);
    fireEvent.click(button);
    await waitFor(() => expect(centres).toContain(ALIAS));
    expect(w.groupHere).toBeTruthy();
  });
});

describe("the other two places that link to a page", () => {
  it("the overview's Not connected tray shows no page link for an alias node", async () => {
    const api = mockApi("table");
    const real = api.webOverview!.bind(api);
    api.webOverview = async (ref, query) => {
      const got = await real(ref, query);
      const base = got.tray[0]!;
      return { ...got, tray: [{ ...base, page: true }, { ...base, id: ALIAS, name: "The Veiled Stranger", page: true }] };
    };
    render(<Web api={api} path="/account/web/100.cmp1.web-lonely" />);
    const button = await screen.findByRole("button", { name: new RegExp("The Veiled Stranger") });
    expect(document.querySelector('a[href^="/account/npc/"]')).not.toBeNull(); // (the real tray name still has its link)
    expect(button.closest("li")!.querySelector("a")).toBeNull();
    expect(document.body.innerHTML).not.toContain("alias%3A");
  });

  it("the names list shows no page link for an alias node, and its row id keeps the whole id", async () => {
    const api = mockApi("table");
    const real = api.webGraph!.bind(api);
    api.webGraph = async (ref, query) => {
      const got = await real(ref, query);
      const base = got.nodes[0]!;
      return { ...got, nodes: [{ ...base, page: true }, { ...base, id: ALIAS, name: "The Veiled Stranger", page: true }] };
    };
    render(<Web api={api} path="/account/web/100.cmp1" />);
    const row = (await screen.findByRole("button", { name: new RegExp("The Veiled Stranger") }, { timeout: 3000 })).closest("li")!;
    fireEvent.click(await screen.findByRole("button", { name: w.expandAll }));
    await waitFor(() => expect(document.querySelector('a[href^="/account/npc/"]')).not.toBeNull());
    expect(row.querySelector("a")).toBeNull();
    expect(row.querySelector(`#web-name-${ALIAS.replace(":", "\\:")}`)).not.toBeNull(); // (an id with a colon is still a valid page id)
    expect(document.body.innerHTML).not.toContain("alias%3A");
  });
});

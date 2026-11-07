/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A connection that has no kind of its own shows the table's words in curly quotes (#3574, website half of #3033), and the
// edit form still starts from the raw words. The API sends `words` already quoted and the raw ones beside them.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { WebGraph } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";

afterEach(cleanup);

const RAW = "sworn on the ashes of";
const QUOTED = `“${RAW}”`;

/** The pretend API with its first connection made a catch-all one, as the real API now sends it. */
function quotedApi() {
  const api = mockApi("table");
  const real = api.webGraph!.bind(api);
  api.webGraph = async (ref, query): Promise<WebGraph> => {
    const got = await real(ref, query);
    // (every edge: whichever one the page opens, its first connection is the catch-all)
    const edges = got.edges.map((edge) => ({ ...edge, label: QUOTED, links: edge.links.map((l, j) => (j === 0 ? { ...l, words: QUOTED, rawWords: RAW } : l)) }));
    return { ...got, edges };
  };
  return api;
}

describe("the table's own words", () => {
  it("shows them in quotes on the connection, and the edit form starts from the raw words", async () => {
    const api = quotedApi();
    render(<Web api={api} path="/account/web/100.cmp1" />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    fireEvent.click(screen.getAllByRole("button", { name: /^See the connections between/ })[0] as HTMLElement);
    const panel = (await screen.findByRole("heading", { level: 2, name: /.+ and .+/ })).closest("section") as HTMLElement;
    await waitFor(() => expect(panel.textContent).toContain(QUOTED));
    const box = within(panel).getByLabelText("Words for connection 1") as HTMLInputElement;
    expect(box.value).toBe(RAW); // (the quotes are for display only)
    // Saving without a change keeps the raw words: no quotes ever reach the stored words.
    fireEvent.click(within(panel).getByRole("button", { name: "Save words for connection 1" }));
    await waitFor(() => expect(api.calls.some((c) => c.includes('"action":"words"'))).toBe(true));
    expect(api.calls.filter((c) => c.includes('"action":"words"')).every((c) => !c.includes("“"))).toBe(true);
  });
});

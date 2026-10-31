/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Who knows each name and connection (#2999 slice 4, #3167): in the DM's full view the page says who knows it in
// five plain states, and "Secret" only when nobody but the DM knows. Without a state the older words still show.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const REF = "100.cmp1";
const at = (suffix = ""): string => `/account/web/${REF}${suffix}`;
async function open(api = mockApi("table")) {
  render(<Web api={api} path={at()} />);
  await screen.findByText(/names?, \d+ lines?/);
  return api;
}
/** The names and connections the mock's full view carries a state for, read the same way the page reads them. */
async function statesOf(api: ReturnType<typeof mockApi>) {
  // The page reads the DM's full web from the centre at two hops (DEFAULT_HOPS), so the states are read the same way.
  const g = await api.webGraph!(REF, { center: null, hops: 2, view: "full" });
  return { nodes: g.nodes.filter((n) => n.knowWord), links: g.edges.flatMap((e) => e.links).filter((l) => l.knowWord) };
}
const row = (name: string): HTMLElement =>
  Array.from(document.querySelectorAll<HTMLElement>(".web-name")).find((li) => li.textContent?.includes(name)) as HTMLElement;

describe("who knows each name and connection", () => {
  it("shows the five states in the list", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    const body = document.body.textContent ?? "";
    for (const word of Object.values(w.knowWords)) expect(body).toContain(word);
  });

  it("shows 🤫 only for secret", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    const { nodes } = await statesOf(api);
    expect(nodes.map((n) => n.knowWord).sort()).toEqual(["hidden_from_the_players", "known_to_an_npc", "known_to_the_party", "partly_revealed", "secret"]);
    for (const n of nodes) {
      const marked = row(n.name).textContent?.includes(w.secretMark) ?? false;
      expect(marked, n.name).toBe(n.knowWord === "secret");
    }
  });

  it("the connection lines say who knows it", async () => {
    const api = await open();
    const { links } = await statesOf(api);
    expect(links.length).toBeGreaterThan(0);
    // The own lines and the further-out lines are two lists.
    const lines = Array.from(document.querySelectorAll(".web-lines")).map((el) => el.textContent ?? "").join(" ");
    for (const l of links) expect(lines).toContain(w.knowWords[l.knowWord!]);
  });

  it("the map marks only secret and the legend says so", async () => {
    const api = await open();
    const { nodes } = await statesOf(api);
    const legend = screen.getByText(`${w.secretMark} ${w.knowWords.secret}`, { exact: false });
    expect(legend).toBeTruthy();
    const mapped = Array.from(document.querySelectorAll<HTMLElement>(".web-node"));
    const label = (name: string) => mapped.find((el) => el.textContent?.includes(name));
    for (const n of nodes) {
      const el = label(n.name);
      if (!el) continue; // a name off the drawn map is not checked here
      expect(el.textContent?.includes(w.secretMark) ?? false, n.name).toBe(n.knowWord === "secret");
    }
  });

  it("with no state the old words still show", async () => {
    const api = mockApi("table");
    const full = api.webGraph!.bind(api);
    // The same full web with the states taken off: what a DM sees from an older API.
    api.webGraph = async (ref, q) => {
      const g = await full(ref, q);
      return { ...g, nodes: g.nodes.map(({ knowWord: _k, ...n }) => n), edges: g.edges.map((e) => ({ ...e, links: e.links.map(({ knowWord: _l, ...l }) => l) })) };
    };
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.expandAll }));
    expect(screen.getAllByText(w.notRevealed, { exact: false }).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain(w.knowHint);
  });

  it("the hint shows only when a state is present", async () => {
    await open();
    expect(document.body.textContent).toContain(w.knowHint);
  });
});

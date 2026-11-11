/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The page side of joining two names (#4155, part 3), with the new panel on: Ctrl+click two names with no connection between them
// opens the panel with an empty list and "+ Add a connection" ready; a connection in a name's panel opens its pair.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/content/connection-panel-switch", () => ({ CONNECTION_PANEL_LIVE: true }));

import type { WebGraph } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { panelWords as p } from "../src/content/connection-panel-words";
import { pairingWords as w } from "../src/content/connection-pairing-words";

afterEach(cleanup);

const graph: WebGraph = {
  center: "n1",
  nodes: [
    { id: "n1", name: "Mira", kind: "npc" },
    { id: "n2", name: "Aldric", kind: "npc" },
    { id: "n3", name: "Brynn", kind: "npc" },
  ],
  edges: [{ a: "n1", b: "n2", label: "trusts", more: 0, links: [{ id: "l1", words: "trusts", secret: false, direction: "a-b" }] }],
  truncated: false,
  maxHops: 6,
  canEdit: true,
};
const page = () => {
  const api = mockApi("table");
  api.webGraph = async () => graph;
  api.webSize = async () => ({ names: 3 });
  render(<Web api={api} path="/account/web/100.cmp1" />);
};
const node = (name: string) => [...document.querySelectorAll(".web-node")].find((n) => n.textContent?.includes(name)) as SVGGElement;

describe("joining two names on the page", () => {
  it("Ctrl+click two names with no connection opens an empty panel ready to add one", async () => {
    page();
    await waitFor(() => expect(node("Brynn")).toBeTruthy());
    fireEvent.click(node("Mira"), { ctrlKey: true });
    expect(screen.getByText(w.pickSecond("Mira"), { exact: false })).toBeTruthy();
    fireEvent.click(node("Brynn"));
    await waitFor(() => expect(screen.getByText(p.none)).toBeTruthy());
    expect(screen.getByRole("button", { name: p.add })).toBeTruthy();
  });

  it("Connect to… does the same for a touch screen", async () => {
    page();
    await waitFor(() => expect(node("Brynn")).toBeTruthy());
    fireEvent.click(node("Mira"));
    fireEvent.click(screen.getByRole("button", { name: w.connectTo }));
    fireEvent.click(node("Brynn"));
    await waitFor(() => expect(screen.getByText(p.none)).toBeTruthy());
  });

  it("a connection in a name's panel opens its pair", async () => {
    page();
    await waitFor(() => expect(node("Aldric")).toBeTruthy());
    fireEvent.click(node("Aldric"));
    fireEvent.click(screen.getByRole("button", { name: /Open this connection/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: p.add })).toBeTruthy());
  });
});

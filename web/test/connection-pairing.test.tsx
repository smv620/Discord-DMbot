/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Joining two names on the map (#4155, part 3): Ctrl/⌘+click the first, then any tap on the second; the "Connect to…" button for
// a phone; and a connection in a name's panel opening its pair. The map only reports the picks; the page decides what opens.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WebMap from "../src/account/WebMap";
import type { WebEdge, WebNode } from "../src/account/api";
import { pairingWords as w } from "../src/content/connection-pairing-words";
import { webPage as words } from "../src/content/web-words";

const nodes: WebNode[] = [
  { id: "m", name: "Mira Voss", kind: "npc" },
  { id: "g", name: "Garrick", kind: "npc" },
];
const edges: WebEdge[] = [{ a: "m", b: "g", label: "serves", more: 0, links: [{ id: "l1", words: "serves", secret: false, direction: "a-b" }] }];
const nodeOf = (name: string) => [...document.querySelectorAll(".web-node")].find((n) => n.textContent?.includes(name)) as SVGGElement;

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function draw(over: { pairFrom?: string | null; canEdit?: boolean } = {}) {
  const onPair = vi.fn();
  const onJump = vi.fn();
  render(
    <WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} onPair={onPair} onJump={onJump} pairFrom={over.pairFrom ?? null} canEdit={over.canEdit ?? true} />,
  );
  return { onPair, onJump };
}

describe("joining two names", () => {
  it("Ctrl+click names the first, and a plain click does not", () => {
    const { onPair } = draw();
    fireEvent.click(nodeOf("Garrick"));
    expect(onPair).not.toHaveBeenCalled();
    fireEvent.click(nodeOf("Garrick"), { ctrlKey: true });
    expect(onPair).toHaveBeenCalledWith("g");
  });

  it("⌘+click works the same", () => {
    const { onPair } = draw();
    fireEvent.click(nodeOf("Mira"), { metaKey: true });
    expect(onPair).toHaveBeenCalledWith("m");
  });

  it("with a first name picked, a plain tap is the second, and the banner says so", async () => {
    const { onPair } = draw({ pairFrom: "m" });
    expect(await screen.findByText(w.pickSecond("Mira Voss"), { exact: false })).toBeTruthy();
    fireEvent.click(nodeOf("Garrick"));
    expect(onPair).toHaveBeenCalledWith("g");
  });

  it("Cancel in the banner gives the first name back (the page cancels on the same name)", async () => {
    const { onPair } = draw({ pairFrom: "m" });
    fireEvent.click(await screen.findByRole("button", { name: w.cancel }));
    expect(onPair).toHaveBeenCalledWith("m");
  });

  it("Connect to… in a name's panel starts with that name", async () => {
    const { onPair } = draw();
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.click(await screen.findByRole("button", { name: w.connectTo }));
    expect(onPair).toHaveBeenCalledWith("g");
  });

  it("a connection in a name's panel opens its pair with that connection", () => {
    const { onJump } = draw();
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.click(screen.getByRole("button", { name: /Open this connection/ }));
    expect(onJump).toHaveBeenCalledWith("m", "g", "l1");
  });

  it("says the tap hint and the label from the words file", () => {
    draw();
    fireEvent.click(nodeOf("Garrick"));
    expect(screen.getByText(words.tapConnection)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.openConnection("Mira Voss serves Garrick") })).toBeTruthy();
  });

  it("a reader who cannot edit gets neither the button nor the jump", () => {
    draw({ canEdit: false });
    fireEvent.click(nodeOf("Garrick"));
    expect(screen.queryByRole("button", { name: w.connectTo })).toBeNull();
    expect(screen.queryByRole("button", { name: /Open this connection/ })).toBeNull();
  });
});

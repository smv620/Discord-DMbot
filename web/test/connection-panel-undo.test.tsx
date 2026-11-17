/** @jsxImportSource preact */
// @vitest-environment happy-dom
// ↩ Undo for a save in the connection panel (#4155, core #4156): each change a save makes gives a batch; one press puts the save back,
// newest change first, and stops at the first one the bot refuses, with the reason in plain words.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConnectionPanel from "../src/account/WebConnectionPanel";
import { mockApi } from "../src/account/mock";
import type { WebEdge, WebLinkChange } from "../src/account/api";
import { panelWords as p } from "../src/content/connection-panel-words";

afterEach(cleanup);

const EDGE: WebEdge = {
  a: "a1",
  b: "b1",
  label: "rival",
  more: 0,
  links: [{ id: "l1", words: "rival", secret: false, direction: "both", pairWords: "are rivals" }],
};
type Act = (c: WebLinkChange, ok: string, own?: Partial<Record<string, string>>, onEdge?: (e: unknown) => void) => Promise<string | null>;

function show(act: Act, canEdit = true) {
  const spy = vi.fn(act);
  render(
    <ConnectionPanel edge={EDGE} nameOf={(id) => (id === "a1" ? "Cerric" : "Wit")} kindOf={() => "npc"} canEdit={canEdit} act={spy as never} reveal={async () => null} revealing={new Set()} api={mockApi("table")} refId="100.cmp1" onClose={() => {}} />,
  );
  return spy;
}
/** An act that answers each change with the next batch number. */
const batching = (refuse?: (c: WebLinkChange) => string | null): Act => {
  let n = 10;
  return async (change, _ok, _own, onEdge) => {
    const said = refuse?.(change) ?? null;
    if (said !== null) return said;
    if (change.action !== "undo") onEdge?.({ ...EDGE, batch: ++n });
    return null;
  };
};
const saveTwo = async () => {
  fireEvent.click(screen.getByRole("button", { name: /Cerric ↔ Wit/ }));
  fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
  fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
  fireEvent.click(screen.getByRole("button", { name: p.save }));
  await screen.findByRole("button", { name: p.undoSave });
};

describe("↩ Undo this save", () => {
  it("is not offered before anything is saved", () => {
    show(batching());
    expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull();
  });

  it("puts the save back, newest change first", async () => {
    const act = show(batching());
    await saveTwo();
    fireEvent.click(screen.getByRole("button", { name: p.undoSave }));
    await waitFor(() => expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull());
    const undone = act.mock.calls.map((c) => c[0]).filter((c) => c.action === "undo");
    expect(undone.map((c) => (c as { batch: number }).batch)).toEqual([12, 11]);
  });

  it("stops at the first refusal, with the reason", async () => {
    const act = show(batching((c) => (c.action === "undo" && c.batch === 11 ? p.undoChanged : null)));
    await saveTwo();
    fireEvent.click(screen.getByRole("button", { name: p.undoSave }));
    await screen.findByText(p.undoChanged);
    expect(act.mock.calls.filter((c) => c[0].action === "undo").map((c) => (c[0] as { batch: number }).batch)).toEqual([12, 11]);
    expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull(); // (nothing older can be put back past it)
  });

  it("is hidden while something new is staged", async () => {
    show(batching());
    await saveTwo();
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are allies" } });
    expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull();
  });
});

describe("the pretend API's undo", () => {
  it("gives each edit a batch, puts back only the newest, and refuses an older one", async () => {
    const api = mockApi("table");
    const ref = "100.cmp1";
    const g = await api.webGraph!(ref, { center: "", hops: 1 });
    const e = g.edges[0]!;
    const id = e.links[0]!.id;
    const first = await api.changeWebLink!(ref, { action: "secret", id, secret: true });
    const second = await api.changeWebLink!(ref, { action: "pin", id, pinned: true });
    expect([first.batch, second.batch]).toEqual([1, 2]);
    await expect(api.changeWebLink!(ref, { action: "undo", id, batch: 1 })).rejects.toMatchObject({ kind: "changed" });
    await api.changeWebLink!(ref, { action: "undo", id, batch: 2 });
    const back = (await api.webGraph!(ref, { center: "", hops: 1 })).edges[0]!.links.find((l) => l.id === id)!;
    expect([back.secret, back.pinned ?? false]).toEqual([true, false]);
  });
});

describe("when an undo is refused", () => {
  it("gives up the older changes with it, and says the reason", async () => {
    const act = show(batching((c) => (c.action === "undo" && c.batch === 12 ? p.undoLate : null)));
    await saveTwo();
    fireEvent.click(screen.getByRole("button", { name: p.undoSave }));
    await screen.findByText(p.undoLate);
    expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull();
    expect(act.mock.calls.filter((c) => c[0].action === "undo")).toHaveLength(1);
  });

  it("always sends the same path id, so a link that went with the first undo cannot break the second", async () => {
    const act = show(batching());
    await saveTwo();
    fireEvent.click(screen.getByRole("button", { name: p.undoSave }));
    await waitFor(() => expect(screen.queryByRole("button", { name: p.undoSave })).toBeNull());
    const ids = act.mock.calls.filter((c) => c[0].action === "undo").map((c) => (c[0] as { id: string }).id);
    expect(new Set(ids).size).toBe(1);
    expect(ids[0]).toMatch(/^[0-9a-f]{32}$/);
  });
});

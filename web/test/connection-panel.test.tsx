/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The redesigned connection panel (#4155): a compact list with the add button on top, a line opened at a time, edits staged until
// Save, a dot where something is unsaved, and a question before leaving with changes pending. A player gets the same list, read-only.
import { useState } from "preact/hooks";
import { act as rendered, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConnectionPanel, { changesOf, type Guard } from "../src/account/WebConnectionPanel";
import { panelWords as p } from "../src/content/connection-panel-words";
import { mockApi } from "../src/account/mock";
import type { WebEdge, WebLinkChange } from "../src/account/api";

afterEach(cleanup);

const link = (id: string, over: Partial<WebEdge["links"][number]> = {}): WebEdge["links"][number] => ({
  id,
  words: "are rivals",
  secret: false,
  direction: "both",
  ...over,
});
const EDGE: WebEdge = {
  a: "a1",
  b: "b1",
  label: "are rivals",
  more: 1,
  links: [link("l1", { words: "rival", pairWords: "are rivals" }), link("l2", { words: "owes a favour to", direction: "a-b", secret: true })],
};
const names: Record<string, string> = { a1: "Cerric", b1: "Wit" };

function show(over: { canEdit?: boolean; act?: (c: WebLinkChange, ok: string) => Promise<string | null>; onClose?: () => void; guard?: { current: Guard | null } } = {}) {
  const act = vi.fn(over.act ?? (async () => null));
  const onClose = over.onClose ?? vi.fn();
  render(
    <ConnectionPanel
      edge={EDGE}
      nameOf={(id) => names[id] ?? id}
      kindOf={() => "npc"}
      canEdit={over.canEdit ?? true}
      act={act as never}
      reveal={async () => null}
      revealing={new Set()}
      api={mockApi("table")}
      refId="100.cmp1"
      onClose={onClose}
      {...(over.guard ? { guard: over.guard } : {})}
    />,
  );
  return { act, onClose };
}
/** The compact line of a connection (its toggle), not the direction button inside an opened one. */
const row = (text: RegExp) => screen.getAllByRole("button", { name: text }).find((b) => b.getAttribute("aria-controls")?.startsWith("web-link-")) as HTMLElement;
const open = (text: RegExp) => fireEvent.click(row(text));

describe("changesOf", () => {
  it("is only what differs from what the connection is now", () => {
    const l = link("l1", { pinned: false });
    expect(changesOf(l, undefined)).toEqual([]);
    expect(changesOf(l, { words: "are rivals", secret: false, pinned: false })).toEqual([]);
    expect(changesOf(l, { words: " are rivals " })).toEqual([]);
    expect(changesOf(l, { words: "are friends", secret: true, pinned: true })).toEqual([
      { action: "words", id: "l1", words: "are friends" },
      { action: "secret", id: "l1", secret: true },
      { action: "pin", id: "l1", pinned: true },
    ]);
  });
});

describe("the list", () => {
  it("has the add button on top and one compact line for each connection, with the arrow, the words and the 🤫", () => {
    show();
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]!.textContent).toBe(p.add);
    expect(row(/Cerric ↔ Wit · are rivals/)).toBeTruthy(); // (a two-way line reads with the API's pair words)
    expect(row(/Cerric → Wit · owes a favour to/).textContent).toContain("🤫");
    expect(screen.getByRole("heading").textContent).toBe("Cerric and Wit");
  });

  it("opens one line at a time", () => {
    show();
    open(/Cerric ↔ Wit/);
    expect(screen.getByLabelText(/Words for connection 1/)).toBeTruthy();
    open(/owes a favour/);
    expect(screen.getByLabelText(/Words for connection 2/)).toBeTruthy();
    expect(screen.queryByLabelText(/Words for connection 1/)).toBeNull();
  });

  it("says so when the two have no connections yet", () => {
    cleanup();
    render(
      <ConnectionPanel edge={{ ...EDGE, links: [] }} nameOf={(id) => id} kindOf={() => "npc"} canEdit act={vi.fn() as never} reveal={async () => null} revealing={new Set()} api={mockApi("table")} refId="r" onClose={() => {}} />,
    );
    expect(screen.getByText(p.none)).toBeTruthy();
    expect(screen.getByRole("button", { name: p.add })).toBeTruthy();
  });
});

describe("staged edits", () => {
  it("wait until Save: a dot on the line and the heading, and nothing sent", () => {
    const { act } = show();
    open(/Cerric ↔ Wit/);
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
    expect(screen.getByRole("heading").textContent).toContain("•");
    expect(act).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy();
  });

  it("Save sends each pending change once and clears the dots", async () => {
    const { act } = show();
    open(/Cerric ↔ Wit/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    fireEvent.click(screen.getByRole("button", { name: p.save })); // (a second press while saving does nothing)
    await waitFor(() => expect(screen.queryByRole("button", { name: p.save })).toBeNull());
    expect(act.mock.calls.map((c) => c[0])).toEqual([
      { action: "secret", id: "l1", secret: true }, // (what keeps the id goes first, then what writes the connection anew)
      { action: "words", id: "l1", words: "are friends" },
    ]);
    expect(row(/Cerric ↔ Wit/).textContent).not.toContain("•");
  });

  it("Discard drops them", () => {
    const { act } = show();
    open(/Cerric ↔ Wit/);
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
    fireEvent.click(screen.getByRole("button", { name: p.discard }));
    expect(row(/Cerric ↔ Wit/).textContent).not.toContain("•");
    expect(act).not.toHaveBeenCalled();
  });

  it("a refused save keeps the changes and says why; a partly done one says how far", async () => {
    let n = 0;
    show({ act: async () => ((n += 1) === 2 ? "The bot said no." : null) });
    open(/Cerric ↔ Wit/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    expect((await screen.findByRole("alert")).textContent).toBe(p.partlySaved(1, 2, "The bot said no."));
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy();
  });
});

describe("leaving with changes pending", () => {
  const dirty = () => {
    open(/Cerric ↔ Wit/);
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
  };

  it("asks before another line opens; Stay keeps everything, Leave drops the changes and opens it", () => {
    show();
    dirty();
    open(/owes a favour/);
    expect(screen.getByText(p.leaveAsk)).toBeTruthy();
    expect(screen.queryByLabelText(/Words for connection 2/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: p.stay }));
    expect(screen.queryByText(p.leaveAsk)).toBeNull();
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
    open(/owes a favour/);
    fireEvent.click(screen.getByRole("button", { name: p.leave }));
    expect(screen.getByLabelText(/Words for connection 2/)).toBeTruthy();
    expect(row(/Cerric ↔ Wit/).textContent).not.toContain("•");
  });

  it("asks before the panel closes", () => {
    const { onClose } = show();
    dirty();
    fireEvent.click(screen.getByRole("button", { name: p.close }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: p.leave }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes at once when nothing is unsaved", () => {
    const { onClose } = show();
    fireEvent.click(screen.getByRole("button", { name: p.close }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText(p.leaveAsk)).toBeNull();
  });

  it("asks for the page too: another name or line on the map, and the browser's own warning", async () => {
    const guard: { current: Guard | null } = { current: null };
    show({ guard });
    const leave = vi.fn();
    expect(guard.current!(leave)).toBe(false); // (nothing unsaved: the page goes on by itself)
    dirty();
    await rendered(async () => {
      expect(guard.current!(leave)).toBe(true);
    });
    expect(leave).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: p.leave }));
    expect(leave).toHaveBeenCalledOnce();
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false); // (the changes were dropped by Leave)
    fireEvent.click(screen.getByRole("button", { name: /owes a favour/ }));
    fireEvent.click(screen.getByRole("button", { name: /Make connection 2 secret/ }));
    const warn = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(warn);
    expect(warn.defaultPrevented).toBe(true);
  });

  it("lets the guard go when the panel is gone", () => {
    const guard: { current: Guard | null } = { current: null };
    show({ guard });
    expect(guard.current).not.toBeNull();
    cleanup();
    expect(guard.current).toBeNull();
  });
});

describe("which way a connection reads", () => {
  it("is one button that shows the state and cycles A → B, B → A, A ↔ B", () => {
    show();
    open(/owes a favour/);
    const toggle = () => screen.getByRole("button", { name: /^Direction: / });
    expect(toggle().textContent).toBe("Cerric → Wit");
    expect(toggle().getAttribute("aria-label")).toBe(p.direction("Cerric to Wit only."));
    expect(screen.getByText(p.directionLabel)).toBeTruthy();
    expect(screen.getByText(`Cerric to Wit only. ${p.directionNext}`)).toBeTruthy(); // (the state in words, beside the arrow)
    fireEvent.click(toggle());
    expect(toggle().textContent).toBe("Cerric ← Wit");
    expect(screen.getByText(`Wit to Cerric only. ${p.directionNext}`)).toBeTruthy();
    expect(row(/Cerric ← Wit/).textContent).toContain("•"); // (staged: the line follows, with its dot)
    fireEvent.click(toggle());
    expect(toggle().textContent).toBe("Cerric ↔ Wit");
    expect(screen.getByText(`Both ways. ${p.directionNext}`)).toBeTruthy();
    fireEvent.click(toggle());
    expect(toggle().textContent).toBe("Cerric → Wit");
    expect(row(/Cerric → Wit/).textContent).not.toContain("•"); // (round again: nothing differs)
  });

  it("is sent with Save, and a turn the rules refuse says so in its own words", async () => {
    const { act } = show({ act: (async (c: WebLinkChange, _ok: string, own?: Record<string, string>) => (c.action === "direction" ? (own?.["bad-target"] ?? "no") : null)) as never });
    open(/owes a favour/);
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    expect((await screen.findByRole("alert")).textContent).toContain(p.cannotTurn);
    expect(act.mock.calls[0]![0]).toEqual({ action: "direction", id: "l2", direction: "b-a" });
  });

  it("goes to the new id when an earlier change wrote the connection anew", async () => {
    // words write the connection anew (l1 becomes l9): the direction after it must be sent to l9, the secret before it to l1
    const calls: WebLinkChange[] = [];
    show({
      act: (async (change: WebLinkChange, _ok: string, _own: unknown, onEdge?: (e: WebEdge) => void) => {
        calls.push(change);
        if (change.action === "words") onEdge?.({ ...EDGE, links: [link("l9", { words: "friends" }), EDGE.links[1]!] });
        return null;
      }) as never,
    });
    open(/are rivals/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls).toEqual([
      { action: "secret", id: "l1", secret: true },
      { action: "words", id: "l1", words: "are friends" },
      { action: "direction", id: "l9", direction: "a-b" },
    ]);
  });
});

/** The panel as the page holds it: the edge is state, and each change is answered with the edge as the API would draw it. */
function Live({ answer }: { answer: (c: WebLinkChange, edge: WebEdge) => { edge: WebEdge; refuse?: string } }) {
  const [edge, setEdge] = useState<WebEdge>(EDGE);
  const act = async (c: WebLinkChange, _ok: string, _own?: unknown, onEdge?: (e: WebEdge) => void): Promise<string | null> => {
    const got = answer(c, edge);
    if (got.refuse) return got.refuse;
    setEdge(got.edge);
    onEdge?.(got.edge);
    return null;
  };
  return (
    <ConnectionPanel edge={edge} nameOf={(id) => names[id] ?? id} kindOf={() => "npc"} canEdit act={act as never} reveal={async () => null} revealing={new Set()} api={mockApi("table")} refId="r" onClose={() => {}} />
  );
}
const withLinks = (edge: WebEdge, links: WebEdge["links"]): WebEdge => ({ ...edge, links });
const stageWordsAndDirection = () => {
  open(/are rivals/);
  fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
  fireEvent.click(screen.getByRole("button", { name: /^Direction: / }));
  fireEvent.click(screen.getByRole("button", { name: p.save }));
};

describe("a save that writes a connection anew", () => {
  it("keeps what is still waiting, on the new id, when a later change is refused", async () => {
    render(
      <Live
        answer={(c, edge) =>
          c.action === "words" ? { edge: withLinks(edge, [link("l9", { words: "friends", rawWords: "friends" }), edge.links[1]!]) } : { edge, refuse: "The bot said no." }
        }
      />,
    );
    stageWordsAndDirection();
    expect((await screen.findByRole("alert")).textContent).toBe(p.partlySaved(1, 2, "The bot said no."));
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy(); // (the direction is still staged, on l9)
    expect(row(/friends/).textContent).toContain("•");
  });

  it("stops and says how far it got when the answer shows more than one new connection", async () => {
    const calls: WebLinkChange[] = [];
    render(
      <Live
        answer={(c, edge) => {
          calls.push(c);
          return { edge: withLinks(edge, [link("n1"), link("n2"), edge.links[1]!]) };
        }}
      />,
    );
    stageWordsAndDirection();
    expect((await screen.findByRole("alert")).textContent).toBe(p.partlySaved(1, 2, p.reloadFirst));
    expect(calls).toHaveLength(1); // (the direction was not sent to a guess)
  });

  it("stops when the connection is gone and no new one shows (it was merged into another)", async () => {
    render(<Live answer={(_c, edge) => ({ edge: withLinks(edge, [edge.links[1]!]) })} />);
    stageWordsAndDirection();
    expect((await screen.findByRole("alert")).textContent).toBe(p.partlySaved(1, 2, p.reloadFirst));
  });

  it("goes on when the id was kept", async () => {
    const calls: WebLinkChange[] = [];
    render(
      <Live
        answer={(c, edge) => {
          calls.push(c);
          return { edge };
        }}
      />,
    );
    stageWordsAndDirection();
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls.map((c) => ("id" in c ? c.id : ""))).toEqual(["l1", "l1"]);
  });

  it("says a connection that cannot read both ways cannot", async () => {
    const { act } = show({ act: (async (c: WebLinkChange, _ok: string, own?: Record<string, string>) => (c.action === "direction" ? (own?.["bad-target"] ?? "no") : null)) as never });
    open(/owes a favour/);
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / })); // → b-a
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / })); // → both
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    expect((await screen.findByRole("alert")).textContent).toContain(p.cannotBoth);
    expect(act.mock.calls[0]![0]).toEqual({ action: "direction", id: "l2", direction: "both" });
  });

  it("a two-way line shows the pair words only when it is saved two-way, and the plain words when staged", () => {
    show();
    open(/owes a favour/);
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / }));
    fireEvent.click(screen.getByRole("button", { name: /^Direction: / })); // → both, not saved
    expect(row(/Cerric ↔ Wit · owes a favour to/)).toBeTruthy();
    expect(screen.queryByText(/are connected/)).toBeNull();
  });
});

describe("while a save is on its way", () => {
  /** A save held open until the test lets it go. */
  function held() {
    let finish: (v: string | null) => void = () => {};
    const calls: WebLinkChange[] = [];
    show({
      act: (change) => {
        calls.push(change);
        return new Promise<string | null>((r) => (finish = r));
      },
    });
    return { calls, finish: (v: string | null = null) => finish(v) };
  }
  const stageTwo = () => {
    open(/Cerric ↔ Wit/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
  };

  it("the fields and toggles are locked, so nothing typed is lost", async () => {
    const { finish } = held();
    stageTwo();
    expect((screen.getByLabelText(/Words for connection 1/) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /Make connection 1 secret/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(p.saving(1, 2), { exact: false })).toBeTruthy();
    finish();
    await waitFor(() => expect(screen.getByText(p.saving(2, 2), { exact: false })).toBeTruthy());
    finish();
    await waitFor(() => expect((screen.getByLabelText(/Words for connection 1/) as HTMLInputElement).disabled).toBe(false));
  });

  it("Discard stops the rest of it from being sent", async () => {
    const { calls, finish } = held();
    stageTwo();
    expect((screen.getByRole("button", { name: p.discard }) as HTMLButtonElement).disabled).toBe(true); // (not while it is with the bot)
    finish();
    await waitFor(() => expect(calls).toHaveLength(2));
    finish();
  });

  it("Leave is off while it is saving", () => {
    const guard: { current: Guard | null } = { current: null };
    const calls: WebLinkChange[] = [];
    show({ guard, act: (c) => (calls.push(c), new Promise<string | null>(() => {})) });
    stageTwo();
    fireEvent.click(screen.getByRole("button", { name: p.close }));
    expect((screen.getByRole("button", { name: p.leave }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a save that throws says so and keeps the changes", async () => {
    show({
      act: async () => {
        throw new Error("boom");
      },
    });
    stageTwo();
    expect((await screen.findByRole("alert")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy();
  });

  it("an empty words field is not a change", () => {
    show();
    open(/Cerric ↔ Wit/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "  " } });
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
  });

  it("Escape closes the question and keeps the changes", () => {
    show();
    open(/Cerric ↔ Wit/);
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.close }));
    fireEvent.keyDown(screen.getByRole("alertdialog"), { key: "Escape" });
    expect(screen.queryByText(p.leaveAsk)).toBeNull();
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
  });
});

describe("a player", () => {
  it("gets the same list, read-only: no add, no inputs, no Save", () => {
    show({ canEdit: false });
    expect(screen.queryByRole("button", { name: p.add })).toBeNull();
    open(/Cerric ↔ Wit/);
    expect(screen.queryByLabelText(/Words for connection/)).toBeNull();
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2);
  });
});

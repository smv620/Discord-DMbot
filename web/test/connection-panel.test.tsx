/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The redesigned connection panel (#4155): a compact list with the add button on top, a line opened at a time, edits staged until
// Save, a dot where something is unsaved, and a question before leaving with changes pending. A player gets the same list, read-only.
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
  links: [link("l1"), link("l2", { words: "owes a favour to", direction: "a-b", secret: true })],
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
const row = (text: RegExp) => screen.getByRole("button", { name: text });
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
    expect(row(/Cerric ↔ Wit · are rivals/)).toBeTruthy();
    expect(row(/Cerric → Wit · owes a favour to/).textContent).toContain("🤫");
    expect(screen.getByRole("heading").textContent).toBe("Cerric and Wit");
  });

  it("opens one line at a time", () => {
    show();
    open(/are rivals/);
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
    open(/are rivals/);
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
    expect(screen.getByRole("heading").textContent).toContain("•");
    expect(act).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy();
  });

  it("Save sends each pending change once and clears the dots", async () => {
    const { act } = show();
    open(/are rivals/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    fireEvent.click(screen.getByRole("button", { name: p.save })); // (a second press while saving does nothing)
    await waitFor(() => expect(screen.queryByRole("button", { name: p.save })).toBeNull());
    expect(act.mock.calls.map((c) => c[0])).toEqual([
      { action: "words", id: "l1", words: "are friends" },
      { action: "secret", id: "l1", secret: true },
    ]);
    expect(row(/Cerric ↔ Wit/).textContent).not.toContain("•");
  });

  it("Discard drops them", () => {
    const { act } = show();
    open(/are rivals/);
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    expect(row(/Cerric ↔ Wit/).textContent).toContain("•");
    fireEvent.click(screen.getByRole("button", { name: p.discard }));
    expect(row(/Cerric ↔ Wit/).textContent).not.toContain("•");
    expect(act).not.toHaveBeenCalled();
  });

  it("a refused save keeps the changes and says why; a partly done one says how far", async () => {
    let n = 0;
    show({ act: async () => ((n += 1) === 2 ? "The bot said no." : null) });
    open(/are rivals/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "are friends" } });
    fireEvent.click(screen.getByRole("button", { name: /Make connection 1 secret/ }));
    fireEvent.click(screen.getByRole("button", { name: p.save }));
    expect((await screen.findByRole("alert")).textContent).toBe(p.partlySaved(1, 2, "The bot said no."));
    expect(screen.getByRole("button", { name: p.save })).toBeTruthy();
  });
});

describe("leaving with changes pending", () => {
  const dirty = () => {
    open(/are rivals/);
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
    open(/are rivals/);
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
    open(/are rivals/);
    fireEvent.input(screen.getByLabelText(/Words for connection 1/), { target: { value: "  " } });
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
  });

  it("Escape closes the question and keeps the changes", () => {
    show();
    open(/are rivals/);
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
    open(/are rivals/);
    expect(screen.queryByLabelText(/Words for connection/)).toBeNull();
    expect(screen.queryByRole("button", { name: p.save })).toBeNull();
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2);
  });
});

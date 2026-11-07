/** @jsxImportSource preact */
// The Story Graph's panel for a person the story never named (#3732): the Names page's own "Give them a name", "Same as
// someone DMbot knows…" and "Remove this person" (NameThem, the same calls and words), and, once one of them has been done,
// the one Undo. It lives in its own file, fetched only when a DM opens such a person, and is kept on the page by the caller
// after the person has become a normal node, so the Undo is still there when the map is read again.
import { useState } from "preact/hooks";

import { notNamedWords as w } from "../content/not-named-words";
import { webPage as words } from "../content/web-words";
import NameThem, { type NamedInfo } from "./NameThem";
import { ApiError, type AccountApi, type NameChange, type NameRow, type WebNode } from "./api";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

/** The Names page's row for this node: the node's label is how the page calls the person ("Ken's sister (name unknown)"). */
function rowOf(node: WebNode): NameRow {
  return { id: node.id, name: node.name, kind: node.kind, others: [], secret: !!node.secret, npcId: null, unnamed: true, ...(node.unnamedWhat ? { unnamedWhat: node.unnamedWhat } : {}), ...(node.unnamedAnchor !== undefined ? { unnamedAnchor: node.unnamedAnchor } : {}) };
}

export default function WebUnnamed({
  node,
  api,
  refId,
  act,
  reload,
  onSignedOut,
  onClose,
}: {
  node: WebNode;
  api: AccountApi;
  refId: string;
  /** The page's own save for a change to a name: used to remove the person. */
  act: (change: NameChange, ok: string) => Promise<string | null>;
  /** Reads the map again. */
  reload: () => Promise<void>;
  onSignedOut: () => void;
  onClose: () => void;
}) {
  const [done, setDone] = useState<NamedInfo | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [looks, setLooks] = useState(0);
  // The map is read again after a step: the panel says so and takes no second press until it has been.
  const [reading, setReading] = useState(false);
  const read = async (): Promise<void> => {
    setReading(true);
    try {
      await reload();
    } finally {
      setReading(false);
    }
  };
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const seen = useObservedWait(undoing, String(looks));

  async function undo(): Promise<void> {
    if (!done?.undo || !api.nameThem || undoing) return;
    setUndoing(true);
    setLooks(0);
    try {
      await api.nameThem(refId, node.id, { action: "name-undo", answer: done.undo }, () => setLooks((n) => n + 1));
      setDone(null);
      setSaid({ ok: true, text: w.undone });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") return onSignedOut();
      // It may still be applied: a second press would queue a second undo of the same step.
      if (kind === "still-waiting") setDone(null);
      setSaid({ ok: false, text: w.undoProblems[kind] ?? (w.undoProblems["server"] as string) });
    } finally {
      setUndoing(false);
    }
    await read();
  }

  return (
    <section class="panel stack" aria-label={words.unnamedHeading(node.name)}>
      {!done && !undoing && !reading && <NameThem id={`unnamed-${node.id}`} row={rowOf(node)} api={api} refId={refId} act={async (change, ok) => { const problem = await act(change, ok); if (problem === null) onClose(); return problem; }} onDone={async (info) => { setDone(info); setSaid(null); await read(); }} onSignedOut={onSignedOut} />}
      {done && !undoing && (
        <div class="stack">
          <p role="status">{w.undoWhat(done.text)}</p>
          <p class="row">
            <button type="button" class="button secondary" onClick={() => void undo()}>
              {done.join ? w.undoJoin : w.undo}
            </button>
            <span class="small muted">{w.undoHint}</span>
          </p>
        </div>
      )}
      {undoing && (
        <WorkProgress stage={looks === 0 ? (w.stage["waiting"] as string) : (w.stage["undoing"] as string)} startedAt={seen.startedAt} updatedAt={seen.movedAt} state={seen.quiet ? "stalled" : "running"} progressUnknown whatToDo={w.quiet} />
      )}
      {reading && <p role="status">{words.reading}</p>}
      {said && (
        <p class={said.ok ? "ok" : "warn"} role={said.ok ? "status" : "alert"}>
          {said.text}
        </p>
      )}
      <button type="button" class="button secondary" disabled={undoing} onClick={onClose}>
        {words.doneShort}
      </button>
    </section>
  );
}

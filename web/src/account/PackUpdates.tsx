/** @jsxImportSource preact */
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1 and P2, the page half of #4872): a newer
// version waiting, or an update DMbot already took, with what changed and an Undo for the taken one. One card per campaign, loaded
// only while RULE_UPDATES_LIVE. The page never takes an update: DMbot takes it at the next session start.
import { useEffect, useState } from "preact/hooks";

import { packUpdateWords as w } from "../content/pack-updates-words";
import { ApiError, type AccountApi, type PackUpdateItem, type PackUpdates } from "./api";

function Pack({
  campaignRef,
  item,
  taken,
  busy,
  onUndo,
}: {
  campaignRef: string;
  item: PackUpdateItem;
  taken: boolean;
  busy: boolean;
  onUndo: (item: PackUpdateItem) => void;
}) {
  const [open, setOpen] = useState(false);
  // The panel's id names the campaign and the pack, since two campaigns can follow one pack (#4872).
  const id = `pack-update-${encodeURIComponent(`${campaignRef}-${item.setId}`)}`;
  return (
    <li class="stack">
      <p class="small">
        <strong>{taken ? w.taken(item.name, item.to) : w.newer(item.name, item.to, item.from)}</strong>
      </p>
      {!taken && <p class="small muted">{w.takesWhen}</p>}
      <div class="row">
        <button type="button" class="button secondary" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((was) => !was)}>
          {open ? w.hide : w.see}
        </button>
        {taken && (
          <button type="button" class="button secondary" disabled={busy} onClick={() => onUndo(item)}>
            {w.undo}
          </button>
        )}
      </div>
      {taken && <p class="small muted">{w.undoLine(item.from)}</p>}
      {open && (
        <div id={id} class="stack">
          <p class="small">{taken ? w.takenChanges : w.changes(item.words)}</p>
          <ul class="small">
            {item.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {item.more > 0 && <p class="small muted">{w.more(item.more)}</p>}
        </div>
      )}
    </li>
  );
}

export default function PackUpdatesCard({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [packs, setPacks] = useState<PackUpdates | null>(null);
  // A failed read keeps the last packs and says so under them; the next read that works clears it (#4872).
  const [failed, setFailed] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function read(): Promise<void> {
    if (!api.packUpdates) return;
    setFailed(false);
    try {
      setPacks(await api.packUpdates(campaignRef));
    } catch {
      setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  // Undo of one taken pack. Busy until the packs are read again, so a stale button can't be pressed twice (#4872).
  async function undo(item: PackUpdateItem) {
    if (busy || !api.packUpdateUndoLast) return;
    setBusy(true);
    setSaid(null);
    try {
      const got = await api.packUpdateUndoLast(campaignRef, item.setId, item.to);
      setSaid({ ok: true, text: w.undone(typeof got.back === "number" ? got.back : item.from) });
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "undo-late" ? w.tooLate : w.couldNot });
    } finally {
      await read();
      setBusy(false);
    }
  }

  if (!api.packUpdates) return null;
  if (!packs) {
    return failed ? (
      <p class="small" role="alert">
        {w.failed}
      </p>
    ) : null;
  }
  // A pack with a newer version waiting shows that, not its taken update.
  const takenOnly = packs.taken.filter((t) => !packs.waiting.some((x) => x.setId === t.setId));
  const nothing = packs.waiting.length === 0 && takenOnly.length === 0;
  if (nothing && !said && !failed) return null;
  return (
    <section class="stack" aria-label={w.heading}>
      <h3>{w.heading}</h3>
      {said && (
        <p class={said.ok ? "ok" : "warn"} role={said.ok ? "status" : "alert"}>
          {said.text}
        </p>
      )}
      {failed && (
        <p class="warn" role="alert">
          {w.failed}
        </p>
      )}
      <ul class="stack">
        {packs.waiting.map((p) => (
          <Pack key={`w-${p.setId}`} campaignRef={campaignRef} item={p} taken={false} busy={busy} onUndo={undo} />
        ))}
        {takenOnly.map((p) => (
          <Pack key={`t-${p.setId}`} campaignRef={campaignRef} item={p} taken busy={busy} onUndo={undo} />
        ))}
      </ul>
    </section>
  );
}

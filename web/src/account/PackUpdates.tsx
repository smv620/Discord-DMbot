/** @jsxImportSource preact */
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1 and P2, the page half of #4872): a newer
// version waiting, or an update DMbot already took, with what changed and an Undo for the taken one. One card per campaign, loaded
// only while RULE_UPDATES_LIVE. The page never takes an update: DMbot takes it at the next session start.
import { useEffect, useState } from "preact/hooks";

import { packUpdateWords as w } from "../content/pack-updates-words";
import { ApiError, type AccountApi, type PackUpdateItem, type PackUpdates } from "./api";

function Pack({ item, taken, busy, onUndo }: { item: PackUpdateItem; taken: boolean; busy: boolean; onUndo: (item: PackUpdateItem) => void }) {
  const [open, setOpen] = useState(false);
  const id = `pack-update-${encodeURIComponent(item.setId)}`;
  return (
    <li class="stack">
      <p class="small">
        <strong>{taken ? w.taken(item.name, item.to) : w.newer(item.name, item.to, item.from)}</strong>
      </p>
      {!taken && <p class="small muted">{w.takesWhen}</p>}
      <div class="row">
        <button type="button" class="button secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((was) => !was)}>
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
  const [failed, setFailed] = useState(false);
  const [round, setRound] = useState(0);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!api.packUpdates) return;
    let live = true;
    api.packUpdates(campaignRef).then(
      (got) => {
        if (live) setPacks(got);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef, round]);

  // Undo of one taken pack: the packs are read again after every try, so what shows is the API's own (#4872).
  async function undo(item: PackUpdateItem) {
    if (busy || !api.packUpdateUndoLast) return;
    setBusy(true);
    setSaid(null);
    try {
      const got = await api.packUpdateUndoLast(campaignRef, item.setId, item.to);
      setSaid({ ok: true, text: w.undone(got.back ?? item.from) });
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "undo-late" ? w.tooLate : w.couldNot });
    } finally {
      setBusy(false);
      setRound((was) => was + 1);
    }
  }

  if (!api.packUpdates) return null;
  if (failed) {
    return (
      <p class="small" role="alert">
        {w.failed}
      </p>
    );
  }
  if (!packs) return null;
  // A pack with a newer version waiting shows that, not its taken update.
  const takenOnly = packs.taken.filter((t) => !packs.waiting.some((x) => x.setId === t.setId));
  const nothing = packs.waiting.length === 0 && takenOnly.length === 0;
  if (nothing && !said) return null;
  return (
    <section class="stack" aria-label={w.heading}>
      <h3>{w.heading}</h3>
      {said && (
        <p class={said.ok ? "ok" : "warn"} role={said.ok ? "status" : "alert"}>
          {said.text}
        </p>
      )}
      <ul class="stack">
        {packs.waiting.map((p) => (
          <Pack key={`w-${p.setId}`} item={p} taken={false} busy={busy} onUndo={undo} />
        ))}
        {takenOnly.map((p) => (
          <Pack key={`t-${p.setId}`} item={p} taken busy={busy} onUndo={undo} />
        ))}
      </ul>
    </section>
  );
}

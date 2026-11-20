/** @jsxImportSource preact */
// The packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1, P2 and P3, the page half of #4872 and
// #4873): a newer version waiting, or an update DMbot already took. A waiting pack can be picked (keep some changes; the rest end the
// follow and become the campaign's own homebrew), and a taken one or a pick can be undone for a day. One card per campaign, loaded only
// while RULE_UPDATES_LIVE. The page never takes an update by itself: DMbot takes it at the next session start.
import { useEffect, useRef, useState } from "preact/hooks";

import { packUpdateWords as w } from "../content/pack-updates-words";
import { ApiError, type AccountApi, type PackUpdateItem, type PackUpdates } from "./api";
import LazyPanel from "./LazyPanel";

// Loaded only once the DM opens the picker (#4873).
const loadPackPick = () => import("./PackPick");

type Done = { name: string; from: number; kept: number; left: number; skipped: number; take: number | null };

/** The plain line for a pick that did not go through, by the bot's word for it (`too_many` arrives as its masters kind here). */
function pickProblem(error: unknown): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "changed") return w.pickChanged;
  if (error.kind === "gone") return w.pickGone;
  if (error.kind === "no-room") return w.pickNoRoom;
  if (error.kind === "too-many-masters") return w.pickTooMany;
  if (error.kind === "still-waiting") return w.pickWaiting;
  return w.couldNot;
}

function Pack({
  api,
  campaignRef,
  item,
  taken,
  busy,
  picking,
  onPick,
  onUndo,
  onStart,
  onPicked,
  onCancel,
  onPickError,
}: {
  api: AccountApi;
  campaignRef: string;
  item: PackUpdateItem;
  taken: boolean;
  busy: boolean;
  picking: boolean;
  onPick: (item: PackUpdateItem) => void;
  onUndo: (item: PackUpdateItem) => void;
  onStart: () => void;
  onPicked: (item: PackUpdateItem, got: { kept: number; left: number; skipped: number; take: number | null }) => void;
  onCancel: () => void;
  onPickError: (error: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  // A picker made for lines that have since changed (or a newer version) closes: its ticks belong to the old lines (#4873).
  const shape = `${item.to}|${item.lines.join("\n")}`;
  const shown = useRef(shape);
  useEffect(() => {
    if (shown.current === shape) return;
    shown.current = shape;
    if (picking) onCancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape]);
  const pickId = `pack-pick-${encodeURIComponent(`${campaignRef}-${item.setId}`)}`;
  // The panel's id names the campaign and the pack, since two campaigns can follow one pack (#4872). It is named only while open.
  const id = `pack-update-${encodeURIComponent(`${campaignRef}-${item.setId}`)}`;
  // The picker is not offered past the list's own length (more than 50 changes) or for a taken update (#4873).
  const canPick = !taken && item.more === 0;
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
        {canPick && (
          <button type="button" class="button secondary" aria-expanded={picking} aria-controls={picking ? pickId : undefined} disabled={busy || !api.packUpdatePick} onClick={() => onPick(item)}>
            {w.pick}
          </button>
        )}
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
      {picking && (
        <LazyPanel
          load={loadPackPick}
          what="the changes you can keep"
          props={{ api, campaignRef, item, id: pickId, onStart, onPicked: (got) => onPicked(item, got), onCancel, onError: onPickError }}
        />
      )}
    </li>
  );
}

/** The key of one pack's picker: its pack and the version it is picked against (#4873). */
const pickKey = (item: PackUpdateItem): string => `${item.setId}:${item.to}`;

export default function PackUpdatesCard({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [packs, setPacks] = useState<PackUpdates | null>(null);
  // A failed read keeps the last packs and says so under them; the next read that works clears it (#4872).
  const [failed, setFailed] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // The pick in progress (by pack), the pick that is done with its Undo, and the words for a pick that did not go through (#4873).
  // The picker is keyed by the pack and its version, so ticks made for an older version can't be sent with a newer one (#4873).
  const [pickingId, setPickingId] = useState<string | null>(null);
  const doneLine = useRef<HTMLParagraphElement>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [pickAlert, setPickAlert] = useState<string | null>(null);

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
  // After a pick the saved line takes focus (#4873).
  useEffect(() => {
    if (done) doneLine.current?.focus();
  }, [done]);

  // Undo of one taken pack. Busy until the packs are read again, so a stale button can't be pressed twice (#4872).
  async function undo(item: PackUpdateItem) {
    if (busy || !api.packUpdateUndoLast) return;
    setBusy(true);
    setSaid(null);
    setPickAlert(null);
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

  // Undo of a pick: the pack is the campaign's own again, back on the version it followed (#4873).
  async function undoPick(d: Done) {
    if (busy || d.take === null || !api.packUpdateUndo) return;
    setBusy(true);
    setSaid(null);
    setPickAlert(null);
    try {
      await api.packUpdateUndo(campaignRef, d.take);
      setDone(null);
      setSaid({ ok: true, text: w.pickUndone(d.from) });
    } catch (error) {
      const tooLate = error instanceof ApiError && error.kind === "undo-late";
      if (tooLate) setDone(null); // past its day: the Undo can only fail again, so it goes
      setSaid({ ok: false, text: tooLate ? w.pickTooLate : w.couldNot });
    } finally {
      await read();
      setBusy(false);
    }
  }

  // The pack's lines go away after a pick; the done line stays above the list. Busy until the packs are read again (#4873).
  async function picked(item: PackUpdateItem, got: { kept: number; left: number; skipped: number; take: number | null }) {
    setPickingId(null);
    setPickAlert(null);
    setSaid(null);
    setDone({ name: item.name, from: item.from, kept: got.kept, left: got.left, skipped: got.skipped, take: got.take });
    setBusy(true);
    await read();
    setBusy(false);
  }

  function pickFailed(error: unknown) {
    setPickAlert(pickProblem(error));
    if (error instanceof ApiError && error.kind === "changed") {
      setPickingId(null); // the pack changed: look at the new changes first
      void read();
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
  if (nothing && !said && !failed && !done) return null;
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
      {pickAlert && !said && (
        <p class="warn" role="alert">
          {pickAlert}
        </p>
      )}
      {done && (
        <div class="stack">
          <p class="ok" role="status" ref={doneLine} tabIndex={-1}>
            {w.done(done.name, done.kept, done.left)}
          </p>
          {done.skipped > 0 && <p class="small">{w.skipped(done.skipped)}</p>}
          {done.take !== null && (
            <>
              <div class="row">
                <button type="button" class="button secondary" disabled={busy} onClick={() => void undoPick(done)}>
                  {w.undo}
                </button>
              </div>
              <p class="small muted">{w.pickUndoLine}</p>
            </>
          )}
        </div>
      )}
      <ul class="stack">
        {packs.waiting.map((p) => (
          <Pack
            key={`w-${p.setId}`}
            api={api}
            campaignRef={campaignRef}
            item={p}
            taken={false}
            busy={busy}
            picking={pickingId === pickKey(p)}
            onPick={(item) => {
              setPickAlert(null);
              setPickingId((was) => (was === pickKey(item) ? null : pickKey(item)));
            }}
            onUndo={undo}
            onStart={() => setPickAlert(null)}
            onPicked={picked}
            onCancel={() => {
              setPickingId(null);
              setPickAlert(null);
            }}
            onPickError={pickFailed}
          />
        ))}
        {takenOnly.map((p) => (
          <Pack
            key={`t-${p.setId}`}
            api={api}
            campaignRef={campaignRef}
            item={p}
            taken
            busy={busy}
            picking={false}
            onPick={() => undefined}
            onUndo={undo}
            onStart={() => undefined}
            onPicked={picked}
            onCancel={() => undefined}
            onPickError={pickFailed}
          />
        ))}
      </ul>
    </section>
  );
}

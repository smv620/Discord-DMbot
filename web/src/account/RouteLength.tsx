/** @jsxImportSource preact */
// A route's length (#4392, page half of #4986): the line, DMbot's read, and the DM's 🛣️ Length form, with ✅ Length is right
// and Undo. Players see the line only, and only once it is there. Drawn only behind ROUTE_LENGTH_LIVE; with it off, nothing shows.
import { useEffect, useState } from "preact/hooks";

import { ROUTE_LENGTH_LIVE } from "../content/route-length-switch";
import { routeLengthWords as w } from "../content/route-length-words";
import { ApiError, type AccountApi, type RouteLength as Length } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string; undo: boolean };

export default function RouteLength({
  api,
  refId,
  on,
  id,
  length,
  route,
  canEdit,
  onChanged,
}: {
  api: AccountApi;
  refId: string;
  on: "connection" | "place";
  id: string;
  length: Length | undefined;
  route: boolean;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [form, setForm] = useState(false);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  // The last change's batch and the length it replaced, so Undo puts that back even before the graph is read again.
  const [undo, setUndo] = useState<{ batch: number; prev: Length | undefined } | null>(null);
  const [local, setLocal] = useState<{ length: Length | undefined } | null>(null);
  useEffect(() => {
    setLocal(null);
  }, [length]);
  if (!ROUTE_LENGTH_LIVE) return null;

  const current = local ? local.length : length;
  // DMbot's read is the DM's to check: a player sees a length only once the DM has kept it (or set their own words).
  const line = current?.words && (canEdit || !current.read) ? w.line(current.words) + (canEdit && current.read ? w.readNote : "") : null;
  const isDm = canEdit && route;

  async function run(action: () => Promise<void>): Promise<void> {
    if (busy) return;
    setBusy(true);
    setSaid(null);
    try {
      await action();
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "";
      setSaid({ ok: false, text: kind === "gone" ? w.gone : kind === "changed" ? w.changed : w.couldNot, undo: false });
    } finally {
      setBusy(false);
      // The page is read again after every outcome: the server may have applied a change that failed on the way back (#5035).
      onChanged();
    }
  }

  const save = () =>
    run(async () => {
      if (!api.changeRouteLength) throw new ApiError("server");
      const typed = words.trim();
      const made = await api.changeRouteLength(refId, { action: "set", on, id, words: typed });
      if (made.batch === null) {
        setSaid({ ok: true, text: w.nothingChanged, undo: false });
        return;
      }
      setLocal({ length: typed ? { words: typed, read: false } : undefined });
      setUndo({ batch: made.batch, prev: current });
      setForm(false);
      setWords("");
      setSaid({ ok: true, text: typed ? w.saved(typed) : w.takenOff, undo: true });
    });

  const keep = () =>
    run(async () => {
      if (!api.changeRouteLength || !current) throw new ApiError("server");
      const made = await api.changeRouteLength(refId, { action: "keep", on, id });
      setLocal({ length: { words: current.words, read: false } });
      if (made.batch !== null) setUndo({ batch: made.batch, prev: current });
      setSaid({ ok: true, text: w.kept(current.words), undo: made.batch !== null });
    });

  // An undo that is too late, or refused because something changed, says so; nothing else about it is claimed.
  const takeBack = async (): Promise<void> => {
    if (busy || undo === null) return;
    setBusy(true);
    setSaid(null);
    try {
      if (!api.changeRouteLength) throw new ApiError("server");
      await api.changeRouteLength(refId, { action: "undo", batch: undo.batch });
      const back = undo;
      setUndo(null);
      setLocal({ length: back.prev });
      setSaid({ ok: true, text: w.undone, undo: false });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "";
      setSaid({ ok: false, text: kind === "undo-late" || kind === "changed" ? w.undoLate : w.couldNot, undo: false });
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const openForm = () => {
    setWords(current?.words ?? "");
    setForm(!form);
  };

  return (
    <div class="stack">
      {line && <p class="small">{line}</p>}
      {isDm && (
        <div class="stack">
          <button type="button" class="button secondary" aria-expanded={form} disabled={busy} onClick={openForm}>
            {w.button}
          </button>
          {form && (
            <div class="stack" role="group" aria-label={w.formHeading}>
              <p class="small muted">{w.formHeading}</p>
              <label>
                {w.label}
                <input
                  type="text"
                  value={words}
                  maxLength={60}
                  placeholder={w.placeholder}
                  disabled={busy}
                  onInput={(e) => setWords((e.target as HTMLInputElement).value)}
                />
              </label>
              <div class="row">
                <button type="button" class="button" disabled={busy} onClick={() => void save()}>
                  {w.save}
                </button>
              </div>
            </div>
          )}
          {current?.read && (
            <button type="button" class="button secondary" disabled={busy} onClick={() => void keep()}>
              {w.keepButton}
            </button>
          )}
        </div>
      )}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {said.undo && undo !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void takeBack()}>
              {w.undo}
            </button>
          )}
        </p>
      )}
      <SavingWait active={busy} />
    </div>
  );
}

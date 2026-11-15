/** @jsxImportSource preact */
// "Add a map" on the Maps page (#4347, slice B): a picture, a name, and the person's yes to the right-to-use question. Nothing is sent
// without that yes. While the upload works it shows the same progress line as the rest of the page (counts and stages only, never the
// picture). Rendered only when the API has the upload (`addMap`), so a pretend or older API shows no form. Words only, from mapsWords.
import { useEffect, useRef, useState } from "preact/hooks";

import { mapsWords as w } from "../content/maps-words";
import { ApiError, type AccountApi, type MapUploadLook } from "./api";
import WorkProgress from "./WorkProgress";

const MAX_BYTES = 10 * 1024 * 1024; // the largest picture the form sends; a bigger one is refused before anything is sent
const STALLED_AFTER = 30; // seconds without a move before the line says it has stalled

export default function MapAddForm({ api, refId, canManage, onAdded }: { api: AccountApi; refId: string; canManage: boolean; onAdded(): void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [look, setLook] = useState<MapUploadLook | null>(null);
  const [since, setSince] = useState<number | null>(null);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const [said, setSaid] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [undoBatch, setUndoBatch] = useState<number | null>(null);
  const flight = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // The clock the progress line counts from, ticking while the upload works.
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  async function yes(): Promise<void> {
    if (flight.current || !file || name.trim() === "") return;
    if (file.size > MAX_BYTES) {
      setProblem(w.badFile(10));
      return;
    }
    flight.current = true;
    setBusy(true);
    setProblem(null);
    setSaid(null);
    setLook(null);
    const start = Math.floor(Date.now() / 1000);
    setSince(start);
    setNow(start);
    try {
      const made = await api.addMap!(refId, { file, title: name.trim(), rights: true }, (latest) => setLook(latest));
      setFile(null);
      setName("");
      if (fileInput.current) fileInput.current.value = "";
      setUndoBatch(made.batch);
      setSaid(canManage ? w.addedDm : w.addedPlayer);
      onAdded();
    } catch (error) {
      setProblem(error instanceof ApiError && error.kind === "bad-file" ? w.badFile(10) : w.network);
    } finally {
      flight.current = false;
      setBusy(false);
      setLook(null);
      setSince(null);
    }
  }

  function no(): void {
    setFile(null);
    setName("");
    if (fileInput.current) fileInput.current.value = "";
    setProblem(null);
    setSaid(w.noSent);
  }

  async function undo(batch: number): Promise<void> {
    if (!api.changeMaps) return setSaid(w.wrong);
    try {
      await api.changeMaps!(refId, { action: "undo", batch });
      setUndoBatch(null);
      setSaid(w.undone);
      onAdded();
    } catch {
      setSaid(w.wrong);
    }
  }

  const stalled = look !== null && look.quiet >= STALLED_AFTER;
  const stage = look === null ? w.sending(0) : stalled ? w.stalled(look.seconds, `${look.quiet} seconds`) : look.stage === "sending" ? w.sending(look.seconds) : w.saving(look.seconds);

  return (
    <section class="stack" aria-label={w.addHeading}>
      <h2 class="small">{w.addHeading}</h2>
      {busy && since !== null && (
        <>
          <WorkProgress stage={stage} startedAt={since} serverNow={now} state={stalled ? "stalled" : "running"} progressUnknown />
          {look !== null && !stalled && look.quiet > 0 && <p class="small muted">{w.lastMoved(look.quiet)}</p>}
        </>
      )}
      <label class="field">
        {w.chooseFile}
        <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={(e) => {
          setProblem(null);
          setFile((e.target as HTMLInputElement).files?.[0] ?? null);
        }} />
      </label>
      <label class="field">
        {w.nameIt}
        <input type="text" maxLength={80} value={name} disabled={busy} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      </label>
      <p class="small">{w.question}</p>
      <p class="small muted">{w.noCheck}</p>
      <p class="row">
        <button type="button" class="button" disabled={busy || !file || name.trim() === ""} onClick={() => void yes()}>{w.yesLabel}</button>
        <button type="button" class="button secondary" disabled={busy} onClick={no}>{w.noLabel}</button>
      </p>
      {said && (
        <p class="row">
          <span class="small" role="status">{said}</span>
          {undoBatch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void undo(undoBatch)}>{w.undoLabel}</button>
          )}
        </p>
      )}
      {problem && <p class="small warn" role="alert">{problem}</p>}
    </section>
  );
}

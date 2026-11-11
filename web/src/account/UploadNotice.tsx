/** @jsxImportSource preact */
// What DMbot added by itself after a lore upload or an import (#3879, website half of #3878; core #3887): "Added 62 names (30
// NPCs, 24 places, …) and 48 connections from <label>.", links to the Names page (opened on what this upload added) and the Story
// Graph, "Undo this upload" for 7 days with one confirmation, and the questions left. While DMbot is still adding, the shared wait
// panel shows the stage, the count, how long and when it last moved; a stalled or failed read says so and keeps the links. Counts
// and the label only: never a name. DM only; nothing is shown when the API sends none, or when nothing was added by itself.
import { useEffect, useRef, useState } from "preact/hooks";

import { autoAddWords as w } from "../content/auto-add-words";
import { ApiError, type AccountApi, type UploadNotice as Notice } from "./api";
import WorkProgress from "./WorkProgress";

const LOOK_MS = 2000;
const LOOKS = 150; // five minutes of looking at an upload that is still being applied, then the page says so

export default function UploadNotice({ api, campaignRef, source, onChanged }: { api: AccountApi; campaignRef: string; source: { kind: "lore" | "import"; id: string }; onChanged?: () => void }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [failed, setFailed] = useState(false);
  const [asking, setAsking] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [undone, setUndone] = useState<{ took: number; of?: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  const yes = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => () => void (live.current = false), []);
  useEffect(() => {
    if (asking) yes.current?.focus();
  }, [asking]);

  // Look until it is no longer being applied; a look that fails says so once and stops.
  useEffect(() => {
    if (!api.uploadNotice) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let n = 0;
    const look = async (): Promise<void> => {
      try {
        const got = await api.uploadNotice!(campaignRef, source);
        if (!alive) return;
        setNotice(got);
        if (got.state === "applying" && ++n < LOOKS) timer = setTimeout(look, LOOK_MS);
      } catch {
        if (alive) setFailed(true);
      }
    };
    void look();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, campaignRef, source.kind, source.id]);

  if (!api.uploadNotice) return null;
  if (failed && !notice) return <p class="muted small">{w.notice.failed}</p>;
  if (!notice || notice.state === "none") return null;

  if (notice.state === "applying") {
    const p = notice.progress;
    const counted = !!p && typeof p.done === "number" && typeof p.total === "number" && p.total > 0;
    return (
      <WorkProgress
        stage={w.applying}
        done={counted ? p!.done : null}
        total={counted ? p!.total : null}
        unit={counted ? "names" : undefined}
        startedAt={p?.startedAt ?? null}
        updatedAt={p?.updatedAt ?? null}
        serverNow={p?.serverNow ?? notice.serverNow}
        state={p?.state === "stalled" ? "stalled" : "running"}
        progressUnknown={!p}
      />
    );
  }

  const days = notice.undoUntil && notice.undoUntil > notice.serverNow ? Math.max(1, Math.ceil((notice.undoUntil - notice.serverNow) / 86400)) : 0;
  const result = undone ?? notice.undone;
  // Core's Names filter knows the website's lore uploads (`u` and the upload's 32-hex id); an import's names are found by session, which
  // that filter does not offer yet, so the link opens the whole Names page for it.
  const fromId = source.kind === "lore" && /^[0-9a-f]{32}$/.test(source.id) ? `u${source.id}` : "";
  const names = `/account/names/${encodeURIComponent(campaignRef)}${fromId ? `#from=${fromId}` : ""}`;

  async function undo(): Promise<void> {
    if (undoing || !api.undoUpload) return;
    setUndoing(true);
    setProblem(null);
    setAsking(false);
    try {
      const got = await api.undoUpload(campaignRef, source);
      if (!live.current) return;
      setUndone(got);
      onChanged?.();
    } catch (error) {
      if (!live.current) return;
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(kind === "undo-late" ? w.undoLate : kind === "undo-done" ? w.undoneAlready : w.notice.failed);
    } finally {
      if (live.current) setUndoing(false);
    }
  }

  return (
    <div class="stack">
      <p role="status">{w.added(notice.counts.names, notice.counts.byKind, notice.counts.connections, notice.label)}</p>
      <ul class="plain-list">
        <li>
          <a href={names}>{w.seeNames}</a>
        </li>
        <li>
          <a href={`/account/web/${encodeURIComponent(campaignRef)}`}>{w.seeGraph}</a>
        </li>
      </ul>
      {notice.questionsLeft > 0 && (
        <p>
          {w.questions(notice.questionsLeft)} <a href="/account/questions">{w.questionsLink}</a>
        </p>
      )}
      {undoing && <p role="status">{w.undoing}…</p>}
      {result && <p role="status">{w.tookBack(result.took, result.of)}</p>}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {!result && days > 0 && !asking && !undoing && (
        <p>
          <button type="button" class="button secondary" ref={first} onClick={() => setAsking(true)}>
            {w.undo}
          </button>{" "}
          <span class="small muted">{w.undoUntil(days)}</span>
        </p>
      )}
      {asking && (
        <div class="stack" role="group" aria-label={w.undo}>
          <p>{w.undoAsk}</p>
          <p>
            <button type="button" class="button" ref={yes} onClick={() => void undo()}>
              {w.undoYes}
            </button>{" "}
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setAsking(false);
                setTimeout(() => first.current?.focus(), 0);
              }}
            >
              {w.undoNo}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

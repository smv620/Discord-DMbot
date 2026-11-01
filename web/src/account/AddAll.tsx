/** @jsxImportSource preact */
// "Add all" under a finished upload on the Upload lore page (#3207, #3222): the DM adds the names the document found
// at once, watches the wait, and can take them back. Behind CONFIRM_KINDS_LIVE (off until the bot's applier is out).
// Only the DM sees it. It never names a thing the document did not find, and ideas are never counted.
import { useEffect, useRef, useState } from "preact/hooks";

import { addAllWords as w, countsList, plainKindSingular, plainKindWord, sumCounts } from "../content/add-all-words";
import { CONFIRM_KINDS_LIVE } from "../content/confirm-kinds-switch";
import { ApiError, type AccountApi, type ConfirmAnswer, type LoreUpload } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const STALL_MS = 60_000;

/** What the last answer said: the names it added or took back, and whether Undo can take them back. */
type Result = { state: "done" | "failed"; action: "add" | "undo"; counts: Record<string, number>; undo: boolean; answer: string };
/** "lost" is a look that failed while the bot may still be working: not the same as nothing added. */
type Problem = "" | "notFound" | "refused" | "failed" | "lost";

function problemOf(error: unknown): Problem {
  if (error instanceof ApiError && error.kind === "not-found") return "notFound";
  if (error instanceof ApiError && error.kind === "not-allowed") return "refused";
  return "failed";
}

export default function AddAll({
  api,
  campaign,
  upload,
  onChanged,
}: {
  api: AccountApi;
  campaign: string;
  upload: LoreUpload;
  /** Called after an add or an Undo is done, so the page reads the waiting counts again. */
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState<"add" | "undo" | null>(null);
  const pending = useRef(false); // set at once on a press, so a second press before the render is ignored
  const [answer, setAnswer] = useState<string | null>(null);
  const [action, setAction] = useState<"add" | "undo">("add");
  const [lastKinds, setLastKinds] = useState<"all" | string[]>("all");
  const [requested, setRequested] = useState(0);
  const [progress, setProgress] = useState<ConfirmAnswer["progress"] | null>(null);
  const [moved, setMoved] = useState(0); // client time of the last forward move
  const [now, setNow] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [problem, setProblem] = useState<Problem>("");
  const lastAt = useRef<number | null>(null);

  // Poll the bot's answer until it is done or failed; the wait panel shows each look.
  useEffect(() => {
    if (!answer || !api.confirmAnswer) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const look = async (): Promise<void> => {
      try {
        const got = await api.confirmAnswer!(campaign, answer);
        if (!live) return;
        if (got.progress && got.progress.at !== lastAt.current) {
          lastAt.current = got.progress.at;
          setMoved(Date.now());
        }
        setProgress(got.progress ?? null);
        setNow(Date.now());
        if (got.state === "waiting" || got.state === "working") {
          timer = setTimeout(look, POLL_MS);
          return;
        }
        setResult({
          state: got.state === "done" ? "done" : "failed",
          action,
          counts: got.counts ?? {},
          undo: got.undo === true,
          answer,
        });
        setAnswer(null);
        pending.current = false;
        setBusy(null);
        if (got.state === "done") onChanged?.();
      } catch (error) {
        if (!live) return;
        // A look that fails may still be working on the bot's side: say so, rather than that nothing was added.
        setProblem(problemOf(error) === "failed" ? "lost" : problemOf(error));
        setAnswer(null);
        pending.current = false;
        setBusy(null);
      }
    };
    void look();
    const ticker = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
      clearInterval(ticker);
    };
    // The answer and the action are the only things that start a new wait.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaign, answer]);

  function start(kind: "add" | "undo", queue: () => Promise<{ answer: string }>, kinds: "all" | string[] | null): void {
    pending.current = true;
    setBusy(kind);
    setAction(kind);
    if (kinds !== null) setLastKinds(kinds);
    setResult(null);
    setProblem("");
    setProgress(null);
    void queue()
      .then((queued) => {
        lastAt.current = null;
        setMoved(Date.now());
        setAnswer(queued.answer);
      })
      .catch((error: unknown) => {
        setProblem(problemOf(error));
        pending.current = false;
        setBusy(null);
      });
  }

  function press(kinds: "all" | string[]): void {
    if (pending.current || !api.confirmKinds) return;
    const waiting = upload.waiting ?? {};
    const chosen = kinds === "all" ? Object.keys(waiting) : kinds;
    setRequested(sumCounts(Object.fromEntries(chosen.map((k) => [k, waiting[k] ?? 0]))));
    start("add", () => api.confirmKinds!(campaign, upload.id, kinds), kinds);
  }

  function undo(): void {
    if (!result || pending.current || !api.confirmUndo) return;
    start("undo", () => api.confirmUndo!(campaign, result.answer), null);
  }

  if (!CONFIRM_KINDS_LIVE || !api.confirmKinds) return null;

  const waiting = upload.waiting ?? {};
  // The biggest kinds first; a tie goes by key, so the buttons do not reorder between reads (#3224).
  const kinds = Object.entries(waiting)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const total = sumCounts(waiting);
  const stalled = busy !== null && now - moved > STALL_MS;
  const checkLink = <a href={`/account/names/${encodeURIComponent(campaign)}`}>{w.checkOneByOne}</a>;
  const added = result?.state === "done" && result.action === "add" ? sumCounts(result.counts) : 0;

  return (
    <div class="stack">
      {total === 0 ? (
        <p role="status">{w.nothingLeft}</p>
      ) : (
        <>
          <p role="status">{w.found(total)}</p>
          <div class="stack">
            <button type="button" class="button" disabled={busy !== null} onClick={() => press("all")}>
              {total === 1 ? w.addOne : w.addAll(total)}
            </button>
            {total > 1 &&
              kinds.length > 1 &&
              kinds.slice(0, 4).map(([k, n]) => (
                <button key={k} type="button" class="button secondary" disabled={busy !== null} onClick={() => press([k])}>
                  {n === 1 ? w.addOneKind(plainKindSingular(k)) : w.addKind(n, plainKindWord(k))}
                </button>
              ))}
            <p>{checkLink}</p>
          </div>
        </>
      )}
      {total === 0 && <p>{checkLink}</p>}
      <p class="small muted">{w.ideas}</p>

      {busy !== null && (
        <>
          <WorkProgress
            stage={busy === "undo" ? w.undoing : w.adding}
            done={null}
            total={null}
            startedAt={progress?.createdAt ?? null}
            updatedAt={progress?.at ?? null}
            serverNow={progress?.now ?? null}
            state={stalled ? "stalled" : "running"}
          />
          {progress && progress.total > 0 && <p class="small">{w.part(progress.done, progress.total)}</p>}
          {stalled && <p role="status">{w.stalled}</p>}
        </>
      )}

      {result?.state === "done" && result.action === "add" && (
        <div class="stack">
          <p role="status">
            {added === 0
              ? w.nothingAdded
              : added === 1
                ? w.added(1, countsList(result.counts))
                : lastKinds !== "all" && lastKinds.length === 1
                  ? w.addedKind(added, plainKindWord(lastKinds[0]!))
                  : w.added(added, countsList(result.counts))}
          </p>
          {result.undo && (
            <button type="button" class="button secondary" disabled={busy !== null} onClick={() => undo()}>
              {w.undo}
            </button>
          )}
        </div>
      )}
      {result?.state === "done" && result.action === "undo" && <p role="status">{w.undone(sumCounts(result.counts))}</p>}
      {result?.state === "failed" && result.action === "add" && (
        <div class="stack">
          <p role="status">{sumCounts(result.counts) > 0 ? w.failedSome(sumCounts(result.counts), requested) : w.failedNone}</p>
          <div class="row">
            <button type="button" class="button" disabled={busy !== null} onClick={() => press(lastKinds)}>
              {w.tryAgain}
            </button>
            {checkLink}
          </div>
        </div>
      )}
      {result?.state === "failed" && result.action === "undo" && (
        <div class="stack">
          <p role="status">{w.undoFailed}</p>
          <div class="row">
            <button type="button" class="button" disabled={busy !== null} onClick={() => undo()}>
              {w.tryAgain}
            </button>
          </div>
        </div>
      )}

      {problem === "notFound" && (
        <p role="status">
          {w.notFound} <a href={`/account/names/${encodeURIComponent(campaign)}`}>{w.openNames}</a>
        </p>
      )}
      {problem === "refused" && <p role="status">{w.refused}</p>}
      {problem === "lost" && (
        <p role="status">
          {w.lostTouch} {checkLink}
        </p>
      )}
      {problem === "failed" && (
        <div class="stack">
          <p role="status">{w.failedNone}</p>
          <div class="row">
            <button type="button" class="button" disabled={busy !== null} onClick={() => press(lastKinds)}>
              {w.tryAgain}
            </button>
            {checkLink}
          </div>
        </div>
      )}
    </div>
  );
}

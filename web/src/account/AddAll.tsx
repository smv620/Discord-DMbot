/** @jsxImportSource preact */
// "Add all" under a finished upload on the Upload lore page (#3207, #3222): the DM adds the names the document found
// at once, watches the wait, and can take them back. Behind CONFIRM_KINDS_LIVE (off until the bot's applier is out).
// Only the DM sees it. It never names a thing the document did not find, and ideas are never counted.
import { useEffect, useRef, useState } from "preact/hooks";

import { addAllWords as w, countsList, parseCounts, plainKindWord, sumCounts } from "../content/add-all-words";
import { CONFIRM_KINDS_LIVE } from "../content/confirm-kinds-switch";
import { ApiError, type AccountApi, type ConfirmAnswer, type LoreUpload } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const STALL_MS = 60_000;

/** What the last answer said: the names it added or took back, and whether Undo can take them back. */
type Result = { state: "done" | "failed"; action: "add" | "undo"; counts: Record<string, number>; undo: boolean; answer: string };
type Problem = "" | "notFound" | "refused" | "failed";

const plainKind = plainKindWord;

function problemOf(error: unknown): Problem {
  if (error instanceof ApiError && error.kind === "not-found") return "notFound";
  if (error instanceof ApiError && error.kind === "not-allowed") return "refused";
  return "failed";
}

export default function AddAll({ api, campaign, upload }: { api: AccountApi; campaign: string; upload: LoreUpload }) {
  const [busy, setBusy] = useState<"add" | "undo" | null>(null);
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
        if (got.state === "running") {
          timer = setTimeout(look, POLL_MS);
          return;
        }
        setResult({
          state: got.state === "done" ? "done" : "failed",
          action,
          counts: parseCounts(got.counts),
          undo: got.undo === true,
          answer,
        });
        setAnswer(null);
        setBusy(null);
      } catch (error) {
        if (!live) return;
        setProblem(problemOf(error));
        setAnswer(null);
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

  async function press(kinds: "all" | string[]): Promise<void> {
    if (busy || !api.confirmKinds) return;
    const waiting = upload.waiting ?? {};
    const chosen = kinds === "all" ? Object.keys(waiting) : kinds;
    setBusy("add");
    setAction("add");
    setLastKinds(kinds);
    setRequested(sumCounts(Object.fromEntries(chosen.map((k) => [k, waiting[k] ?? 0]))));
    setResult(null);
    setProblem("");
    setProgress(null);
    try {
      const queued = await api.confirmKinds(campaign, upload.id, kinds);
      lastAt.current = null;
      setMoved(Date.now());
      setAnswer(queued.answer);
    } catch (error) {
      setProblem(problemOf(error));
      setBusy(null);
    }
  }

  async function undo(): Promise<void> {
    if (!result || busy || !api.confirmUndo) return;
    setBusy("undo");
    setAction("undo");
    setResult(null);
    setProblem("");
    setProgress(null);
    try {
      const queued = await api.confirmUndo(campaign, result.answer);
      lastAt.current = null;
      setMoved(Date.now());
      setAnswer(queued.answer);
    } catch (error) {
      setProblem(problemOf(error));
      setBusy(null);
    }
  }

  if (!CONFIRM_KINDS_LIVE || !api.confirmKinds) return null;

  const waiting = upload.waiting ?? {};
  const kinds = Object.entries(waiting)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = sumCounts(waiting);
  const stalled = busy !== null && now - moved > STALL_MS;
  const checkLink = <a href={`/account/names/${encodeURIComponent(campaign)}`}>{w.checkOneByOne}</a>;

  return (
    <div class="stack">
      {total === 0 ? (
        <p role="status">{w.nothingLeft}</p>
      ) : (
        <>
          <p role="status">{w.found(total)}</p>
          <div class="stack">
            {total === 1 ? (
              <button type="button" class="button" disabled={busy !== null} onClick={() => void press("all")}>
                {w.addOne}
              </button>
            ) : (
              <button type="button" class="button" disabled={busy !== null} onClick={() => void press("all")}>
                {w.addAll(total)}
              </button>
            )}
            {total > 1 &&
              kinds.length > 1 &&
              kinds.slice(0, 4).map(([k, n]) => (
                <button key={k} type="button" class="button secondary" disabled={busy !== null} onClick={() => void press([k])}>
                  {w.addKind(n, plainKind(k))}
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
            {sumCounts(result.counts) === 1
              ? w.added(1, countsList(result.counts))
              : lastKinds !== "all" && lastKinds.length === 1
                ? w.addedKind(sumCounts(result.counts), plainKind(lastKinds[0]!))
                : w.added(sumCounts(result.counts), countsList(result.counts))}
          </p>
          {result.undo && (
            <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void undo()}>
              {w.undo}
            </button>
          )}
        </div>
      )}
      {result?.state === "done" && result.action === "undo" && (
        <p role="status">{w.undone(sumCounts(result.counts))}</p>
      )}
      {result?.state === "failed" && (
        <div class="stack">
          <p role="status">
            {sumCounts(result.counts) > 0 ? w.failedSome(sumCounts(result.counts), requested) : w.failedNone}
          </p>
          <div class="row">
            <button type="button" class="button" disabled={busy !== null} onClick={() => void press(lastKinds)}>
              {w.tryAgain}
            </button>
            {checkLink}
          </div>
        </div>
      )}

      {problem === "notFound" && (
        <p role="status">
          {w.notFound} <a href={`/account/names/${encodeURIComponent(campaign)}`}>{w.openNames}</a>
        </p>
      )}
      {problem === "refused" && <p role="status">{w.refused}</p>}
      {problem === "failed" && (
        <div class="stack">
          <p role="status">{w.failedNone}</p>
          <div class="row">
            <button type="button" class="button" disabled={busy !== null} onClick={() => void press(lastKinds)}>
              {w.tryAgain}
            </button>
            {checkLink}
          </div>
        </div>
      )}
    </div>
  );
}

/** @jsxImportSource preact */
// One button that makes and downloads a campaign's backup, with its progress (#3148). It is the backup part of
// "Delete this campaign" (#2555), moved here so the campaign's page can offer a backup on its own and the two cannot
// drift apart: the same call (`api.campaignBackup`), the same stages, "2 of 7 files", how long it has run, and a
// stalled notice. DMbot's words for the wait are the Delete box's (content/campaign-delete.ts), which already hold
// the stage names and the failures.
import { useEffect, useRef, useState } from "preact/hooks";

import { BACKUP_HAS_TRANSCRIPTS, campaignDeleteWords as words } from "../content/campaign-delete";
import { stageWords } from "../content/work-progress";
import { ApiError, type AccountApi, type JobProgress } from "./api";
import WorkProgress from "./WorkProgress";

export default function CampaignBackup({
  api,
  refId,
  label,
  warnNoTranscripts = false,
  onWorking,
  onGone,
}: {
  api: AccountApi;
  refId: string;
  /** The button's words ("Download a backup", or "Download a backup first" in the Delete box). */
  label: string;
  /** Say that transcripts are not in the backup yet, while that is so (the Delete box needs it). */
  warnNoTranscripts?: boolean;
  /** Told when a backup starts and ends, so the Delete box can keep its own button off meanwhile. */
  onWorking?: (working: boolean) => void;
  /** The campaign was already gone: the page reads itself again. */
  onGone?: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState("");
  const [saved, setSaved] = useState(false);
  // How far DMbot has got with the backup (its own answer: stage, how long, last moved).
  const [made, setMade] = useState<JobProgress | null>(null);
  // Then how many of its files are fetched ("2 of 7 files"), on this browser's own clock.
  const [fetched, setFetched] = useState<JobProgress | null>(null);
  const [savedNames, setSavedNames] = useState<string[]>([]);
  const [big, setBig] = useState(false);
  const askedAt = useRef(0); // when the backup was asked for: the clock of the whole wait, files included
  const [tick, setTick] = useState(0); // redraw so a stuck file is noticed
  const busy = useRef(false);

  const backup = async () => {
    if (busy.current || !api.campaignBackup) return;
    busy.current = true;
    setWorking(true);
    onWorking?.(true);
    setProblem("");
    setSaved(false);
    setMade(null);
    setFetched(null);
    setBig(false);
    askedAt.current = Math.floor(Date.now() / 1000);
    try {
      const set = await api.campaignBackup(refId, setMade, (done, total) => {
        const now = Math.floor(Date.now() / 1000);
        setFetched({ stage: "fetching", state: "running", done, total, unit: words.filesUnit(total), startedAt: askedAt.current, updatedAt: now, serverNow: now });
      });
      // One download after another, a moment apart, so a browser does not mistake them for a pile of pop-ups.
      for (const [i, file] of set.files.entries()) {
        if (i > 0) await new Promise((resolve) => setTimeout(resolve, 300));
        const href = URL.createObjectURL(file.blob);
        const link = document.createElement("a");
        link.href = href;
        link.download = file.name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Not at once: some browsers cancel a download whose address is taken back in the same breath.
        setTimeout(() => URL.revokeObjectURL(href), 30_000);
      }
      setSavedNames(set.files.map((f) => f.name));
      setBig(set.files.some((f) => f.blob.size > words.bigFileBytes));
      setSaved(true);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(words.problem[kind] ?? words.problem["server"] ?? "");
      if (kind === "not-found") onGone?.();
    } finally {
      askedAt.current = 0;
      busy.current = false;
      setWorking(false);
      onWorking?.(false);
    }
  };

  // While files are being fetched, a quiet minute is shown as stalled, with what to do (#2564).
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setTick((n) => n + 1), 5000);
    return () => clearInterval(timer);
  }, [working]);
  void tick;
  const quietFor = fetched ? Math.floor(Date.now() / 1000) - (fetched.updatedAt ?? 0) : 0;

  return (
    <>
      {warnNoTranscripts && !BACKUP_HAS_TRANSCRIPTS && <p class="small warn">{words.backupWithoutTranscripts}</p>}
      <div class="row">
        <button type="button" class="button" disabled={working} onClick={() => void backup()}>
          {working ? words.backingUp : label}
        </button>
      </div>
      {working && (
        <WorkProgress
          stage={fetched ? stageWords(words.stage, fetched.stage) : made ? stageWords(words.stage, made.stage) : words.backingUp}
          done={fetched?.done}
          total={fetched?.total}
          unit={fetched?.unit ?? undefined}
          startedAt={fetched ? fetched.startedAt : made?.startedAt}
          updatedAt={fetched ? fetched.updatedAt : made?.updatedAt}
          serverNow={fetched ? fetched.serverNow : made?.serverNow}
          state={fetched ? (quietFor > 60 ? "stalled" : "running") : made && made.state !== "done" ? made.state : "running"}
          whatToDo={fetched && quietFor > 60 ? words.fileSlow : undefined}
        />
      )}
      <p class="small">{words.backupNote}</p>
      {saved && (
        <div role="status" class="small">
          <p>{words.backupDone(savedNames)}</p>
          {savedNames.length > 1 && (
            <ul>
              {savedNames.map((file) => (
                <li key={file}>{file}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {saved && big && <p class="small muted">{words.backupBigFile}</p>}
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
    </>
  );
}

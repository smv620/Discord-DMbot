/** @jsxImportSource preact */
// "Delete this campaign" at the bottom of a campaign's page, and on the My Account card of a campaign whose server
// DMbot can't reach (#2555; core #2554; #2879). The same steps as Discord's button: one warning that says what goes
// and that it can't be undone, a backup offered first, and the campaign's name typed to confirm. It is offered to the plan holder only (the API checks again), and the
// button stays off until the typed name matches, as Discord's box does (any case, extra spaces ignored).
import { useEffect, useRef, useState } from "preact/hooks";

import { BACKUP_HAS_TRANSCRIPTS, campaignDeleteWords as words } from "../content/campaign-delete";
import { ApiError, type AccountApi, type JobProgress } from "./api";
import { stageWords } from "../content/work-progress";
import WorkProgress from "./WorkProgress";

const squash = (name: string): string => name.split(/\s+/).filter(Boolean).join(" ").toLocaleLowerCase();

export default function CampaignDelete({
  api,
  refId,
  name,
  tryIt = false,
  onDeleted,
  onGone,
}: {
  api: AccountApi;
  refId: string;
  name: string;
  /** A Try It campaign has no backup, so none is offered. */
  tryIt?: boolean;
  /** Called once the API says it deleted the campaign, so the list can say so and read itself again. */
  onDeleted: (channelsLeft: boolean) => void;
  /** The campaign was already gone (not deleted by this press): the list reads itself again, and says
   * so without claiming a delete. */
  onGone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [state, setState] = useState<"idle" | "backup" | "deleting">("idle");
  const [problem, setProblem] = useState("");
  const [saved, setSaved] = useState(false);
  const [kept, setKept] = useState(false);
  // How far DMbot has got with the backup (its own answer: stage, how long, last moved).
  const [made, setMade] = useState<JobProgress | null>(null);
  // Then how many of its files are fetched ("2 of 7 files"), on this browser's own clock.
  const [fetched, setFetched] = useState<JobProgress | null>(null);
  const [savedNames, setSavedNames] = useState<string[]>([]);
  const [big, setBig] = useState(false);
  const askedAt = useRef(0); // when the backup was asked for: the clock of the whole wait, files included
  const [tick, setTick] = useState(0); // redraw so a stuck file is noticed
  const working = useRef(false);
  const opener = useRef<HTMLButtonElement>(null);
  const boxId = `delete-${refId}`;

  const fail = (error: unknown) => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(words.problem[kind] ?? words.problem["server"] ?? "");
    if (kind === "not-found") onGone(); // never "Deleted": this press deleted nothing
  };

  const backup = async () => {
    if (working.current || !api.campaignBackup) return;
    working.current = true;
    setState("backup");
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
      fail(error);
    } finally {
      askedAt.current = 0;
      working.current = false;
      setState("idle");
    }
  };

  const remove = async () => {
    if (working.current || squash(typed) !== squash(name)) return;
    working.current = true;
    setState("deleting");
    setProblem("");
    try {
      const done = await api.deleteCampaign!(refId, typed.trim());
      onDeleted(done.channelsLeft);
    } catch (error) {
      fail(error);
    } finally {
      working.current = false;
      setState("idle");
    }
  };

  // While files are being fetched, a quiet minute is shown as stalled, with what to do (#2564).
  useEffect(() => {
    if (state !== "backup") return;
    const timer = setInterval(() => setTick((n) => n + 1), 5000);
    return () => clearInterval(timer);
  }, [state]);
  void tick;
  const quietFor = fetched ? Math.floor(Date.now() / 1000) - (fetched.updatedAt ?? 0) : 0;
  const matches = squash(typed) === squash(name);
  const backups = !tryIt && !!api.campaignBackup;
  if (!open) {
    return (
      <>
        <p>
          <button ref={opener} type="button" class="button secondary" aria-label={words.buttonFor(name)} onClick={() => { setKept(false); setOpen(true); }}>
            {words.button}
          </button>
        </p>
        {kept && <p role="status" class="small">{words.kept(name)}</p>}
      </>
    );
  }
  return (
    <div class="panel stack" role="group" aria-labelledby={`${boxId}-title`}>
      <h4 id={`${boxId}-title`}>{words.heading(name)}</h4>
      <p>{words.warning(name)}</p>
      <p class="small">{words.warningStays}</p>
      {backups && (
        <>
          {!BACKUP_HAS_TRANSCRIPTS && <p class="small warn">{words.backupWithoutTranscripts}</p>}
          <div class="row">
            {/* First, so the way to keep a copy comes before the way to delete. */}
            <button type="button" class="button" disabled={state !== "idle"} onClick={() => void backup()}>
              {state === "backup" ? words.backingUp : words.backup}
            </button>
          </div>
          {state === "backup" && (
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
        </>
      )}
      <label class="field">
        {words.typeName}
        <input type="text" value={typed} maxLength={100} placeholder={name} autoComplete="off" autoCapitalize="off" autoCorrect="off" spellcheck={false} onInput={(e) => setTyped((e.target as HTMLInputElement).value)} />
      </label>
      <p class="small muted">{typed.trim() !== "" && !matches ? words.noMatch : words.typeHint}</p>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      <div class="row">
        <button
          type="button"
          class="button secondary"
          disabled={state === "deleting"}
          onClick={() => {
            setKept(true);
            setOpen(false);
            setTyped("");
            setProblem("");
            setTimeout(() => opener.current?.focus(), 0);
          }}
        >
          {words.keep}
        </button>
        <button
          type="button"
          class="button danger"
          disabled={state !== "idle" || !matches}
          aria-busy={state === "deleting"}
          onClick={() => void remove()}
        >
          {state === "deleting" ? words.deleting : words.confirm}
        </button>
      </div>
    </div>
  );
}

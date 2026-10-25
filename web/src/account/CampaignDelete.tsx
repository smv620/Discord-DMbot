/** @jsxImportSource preact */
// "Delete this campaign" on a campaign's card in My Account (#2555; core #2554). The same steps as Discord's
// button: one warning that says what goes and that it can't be undone, a backup offered first, and the
// campaign's name typed to confirm. It is offered to the plan holder only (the API checks again), and the
// button stays off until the typed name matches, as Discord's box does (any case, extra spaces ignored).
import { useRef, useState } from "preact/hooks";

import { BACKUP_HAS_TRANSCRIPTS, campaignDeleteWords as words } from "../content/campaign-delete";
import { ApiError, type AccountApi } from "./api";
import { useObservedWait } from "./useObservedWait";
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
    try {
      const blob = await api.campaignBackup(refId);
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = words.backupFileName(name);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setSaved(true);
    } catch (error) {
      fail(error);
    } finally {
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

  const backingUp = useObservedWait(state === "backup", "backup");
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
          {state === "backup" && backingUp.startedAt !== null && (
            <WorkProgress
              stage={words.backingUp}
              startedAt={backingUp.startedAt}
              updatedAt={backingUp.movedAt}
              serverNow={Math.floor(Date.now() / 1000)}
              state="running"
            />
          )}
          <p class="small">{words.backupNote}</p>
          {saved && <p role="status" class="small">{words.backupDone(words.backupFileName(name))}</p>}
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

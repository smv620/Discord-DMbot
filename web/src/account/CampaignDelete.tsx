/** @jsxImportSource preact */
// "Delete this campaign" at the bottom of a campaign's page, and on the My Account card of a campaign whose server
// DMbot can't reach (#2555; core #2554; #2879). The same steps as Discord's button: one warning that says what goes
// and that it can't be undone, a backup offered first, and the campaign's name typed to confirm. It is offered to the plan holder only (the API checks again), and the
// button stays off until the typed name matches, as Discord's box does (any case, extra spaces ignored).
import { useRef, useState } from "preact/hooks";

import { campaignDeleteWords as words } from "../content/campaign-delete";
import { ApiError, type AccountApi } from "./api";
import CampaignBackup from "./CampaignBackup";

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
  const [state, setState] = useState<"idle" | "deleting">("idle");
  const [backingUp, setBackingUp] = useState(false); // a backup is being made (its own button, below)
  const [problem, setProblem] = useState("");
  const [kept, setKept] = useState(false);
  const working = useRef(false);
  const opener = useRef<HTMLButtonElement>(null);
  const boxId = `delete-${refId}`;

  const fail = (error: unknown) => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(words.problem[kind] ?? words.problem["server"] ?? "");
    if (kind === "not-found") onGone(); // never "Deleted": this press deleted nothing
  };

  const remove = async () => {
    if (working.current || backingUp || squash(typed) !== squash(name)) return;
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
        /* First, so the way to keep a copy comes before the way to delete. */
        <CampaignBackup api={api} refId={refId} label={words.backup} inDelete onWorking={setBackingUp} onGone={onGone} />
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
          disabled={state === "deleting" || backingUp}
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
          disabled={state !== "idle" || backingUp || !matches}
          aria-busy={state === "deleting"}
          onClick={() => void remove()}
        >
          {state === "deleting" ? words.deleting : words.confirm}
        </button>
      </div>
    </div>
  );
}

/** @jsxImportSource preact */
// "See changes" and "Pick changes" under a followed house-rules set (#4841 C1–C2, #4845, #4855): a newer version of the set is
// waiting for the campaign, with what would change. Its own file, loaded only from the House rules page while RULE_UPDATES_LIVE.
// Nothing is drawn when no update waits for this set. The result of a pick and its Undo stay on screen after the follow has ended
// (the update read then comes back null), so they are kept above that check. Nothing is taken by this card on its own.
import { useEffect, useState } from "preact/hooks";

import { ruleUpdateWords as w } from "../content/rule-updates-words";
import { ApiError, type AccountApi, type RuleUpdate as Update } from "./api";
import LazyPanel from "./LazyPanel";

// Loaded only once the DM opens the picker (#4855).
const loadRulePick = () => import("./RulePick");

type Result = { kind: "done"; name: string; from: number; kept: number; left: number; take: number | null } | { kind: "undone"; name: string; from: number };

/** The plain line for a pick that did not go through: the bot's own word for it, or the plain one. */
function pickProblem(error: unknown): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "changed") return w.changed;
  if (error.kind === "gone") return w.gone;
  if (error.kind === "no-room") return w.noRoom;
  return w.couldNot;
}

export default function RuleUpdate({ api, campaignRef, name }: { api: AccountApi; campaignRef: string; name: string }) {
  const [update, setUpdate] = useState<Update | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const [round, setRound] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [undoing, setUndoing] = useState(false);

  useEffect(() => {
    if (!api.ruleUpdate) return;
    let live = true;
    api.ruleUpdate(campaignRef).then(
      (got) => {
        if (!live) return;
        setUpdate(got.update);
        setLoaded(true);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef, round]);

  function picked(got: { kept: number; left: number; take: number | null }) {
    setPicking(false);
    setAlert(null);
    setResult({ kind: "done", name, from: update?.from ?? 0, kept: got.kept, left: got.left, take: got.take });
    setRound((was) => was + 1); // the update is read again for the list; the result stays above it
  }

  function pickFailed(error: unknown) {
    setAlert(pickProblem(error));
    if (error instanceof ApiError && error.kind === "changed") {
      setPicking(false); // the set changed: look at the new changes first
      setRound((was) => was + 1);
    }
  }

  async function undo(take: number) {
    if (undoing || !api.ruleUpdateUndo) return;
    setUndoing(true);
    setAlert(null);
    try {
      await api.ruleUpdateUndo(campaignRef, take);
      setResult((was) => (was?.kind === "done" ? { kind: "undone", name, from: was.from } : was));
    } catch (error) {
      setAlert(error instanceof ApiError && error.kind === "undo-late" ? w.tooLate : w.couldNot);
    } finally {
      setUndoing(false);
    }
  }

  if (!api.ruleUpdate) return null;
  const alertLine = alert ? (
    <p class="small" role="alert">
      {alert}
    </p>
  ) : null;

  if (result) {
    return (
      <div class="stack">
        {alertLine}
        {result.kind === "done" ? (
          <>
            <p class="small">{w.done(result.name, result.kept, result.left)}</p>
            <div class="row">
              <button type="button" class="button secondary" disabled={undoing || result.take === null} onClick={() => result.take !== null && void undo(result.take)}>
                {w.undo}
              </button>
            </div>
            <p class="small muted">{w.undoLine}</p>
          </>
        ) : (
          <p class="small">{w.undone(result.from)}</p>
        )}
      </div>
    );
  }

  if (failed) {
    return (
      <p class="small" role="alert">
        {w.failed}
      </p>
    );
  }
  if (!loaded || !update || update.name !== name) return alertLine;

  const id = `rule-update-${encodeURIComponent(`${campaignRef}-${name}`)}`;
  return (
    <div class="stack">
      {alertLine}
      <p class="small">{w.newer(update.name, update.to, update.from)}</p>
      <p class="small muted">{w.takesWhen}</p>
      <div class="row">
        <button type="button" class="button secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((was) => !was)}>
          {open ? w.hideChanges : w.seeChanges}
        </button>
        {update.more === 0 && (
          <button type="button" class="button secondary" aria-expanded={picking} disabled={!api.ruleUpdatePick} onClick={() => setPicking((was) => !was)}>
            {w.pick}
          </button>
        )}
      </div>
      {update.more > 0 && <p class="small muted">{w.tooMany}</p>}
      {open && (
        <div id={id} class="stack">
          <p class="small">{w.changes(update.words)}</p>
          <ul class="small">
            {update.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {update.more > 0 && <p class="small muted">{w.more(update.more)}</p>}
        </div>
      )}
      {picking && (
        <LazyPanel
          load={loadRulePick}
          what="the changes you can keep"
          props={{ api, campaignRef, update, onPicked: picked, onCancel: () => setPicking(false), onError: pickFailed }}
        />
      )}
    </div>
  );
}

/** @jsxImportSource preact */
// "See changes" and "Pick changes" under a followed house-rules set (#4841 C1–C2, #4845, #4855), and "Undo" for an update DMbot
// already took (#4867). A newer version waiting for the campaign shows with what would change; when DMbot has taken one instead,
// its changes show with an Undo for a day. Its own file, loaded only from the House rules page while RULE_UPDATES_LIVE. Nothing is
// drawn when no update waits or was taken for this set. The result of a pick or an Undo stays on screen after the follow has
// changed (the reads then come back empty), so it is kept above those checks.
import { useEffect, useRef, useState } from "preact/hooks";

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
  // Focus: the Pick button returns focus when the picker closes; the saved line takes it after a pick (#4884).
  const pickButton = useRef<HTMLButtonElement | null>(null);
  const doneLine = useRef<HTMLParagraphElement | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [taken, setTaken] = useState<Update | null>(null);
  const [takenFailed, setTakenFailed] = useState(false);
  const [takenOpen, setTakenOpen] = useState(false);

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

  // An update DMbot already took is read only while no newer one waits for this set (#4867).
  useEffect(() => {
    if (!api.ruleTaken || !loaded || (update && update.name === name)) {
      setTaken(null);
      setTakenFailed(false);
      return;
    }
    let live = true;
    api.ruleTaken(campaignRef).then(
      (got) => {
        if (!live) return;
        setTaken(got.taken);
        setTakenFailed(false);
      },
      () => {
        if (live) setTakenFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef, name, loaded, update, round]);

  // After a pick the saved line takes focus (#4884).
  useEffect(() => {
    if (result?.kind === "done") doneLine.current?.focus();
  }, [result]);

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

  // Undo of the update DMbot took (#4867): back to the version before it, which the card then says.
  async function undoTaken(t: Update) {
    if (undoing || !api.ruleUpdateUndoLast) return;
    setUndoing(true);
    setAlert(null);
    try {
      const got = await api.ruleUpdateUndoLast(campaignRef, t.to);
      setTaken(null);
      setResult({ kind: "undone", name, from: got.back ?? t.from });
      setRound((was) => was + 1);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "undo-late") {
        // Too late: read both again, so the Undo goes away once the update is no longer taken (#4867).
        setAlert(w.tooLateTaken);
        setRound((was) => was + 1);
      } else {
        setAlert(w.couldNot);
      }
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
            <p class="small" role="status" ref={doneLine} tabIndex={-1}>
              {w.done(result.name, result.kept, result.left)}
            </p>
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
  if (!loaded) return alertLine;

  // No newer version waits for this set: an update DMbot already took can still be undone (#4867).
  if (!update || update.name !== name) {
    const showTaken = taken !== null && taken.name === name;
    if (!showTaken && !alert && !takenFailed) return null;
    const takenId = `rule-taken-${encodeURIComponent(`${campaignRef}-${name}`)}`;
    return (
      <div class="stack">
        {alertLine}
        {takenFailed && (
          <p class="small" role="alert">
            {w.failed}
          </p>
        )}
        {showTaken && taken && (
          <>
            <p class="small">{w.taken(taken.name, taken.to)}</p>
            <div class="row">
              <button type="button" class="button secondary" aria-expanded={takenOpen} aria-controls={takenId} onClick={() => setTakenOpen((was) => !was)}>
                {takenOpen ? w.hideChanges : w.seeChanges}
              </button>
              <button type="button" class="button secondary" disabled={undoing} onClick={() => void undoTaken(taken)}>
                {w.undo}
              </button>
            </div>
            <p class="small muted">{w.undoTakenLine(taken.from)}</p>
            {takenOpen && (
              <div id={takenId} class="stack">
                <p class="small">{w.takenChanges}</p>
                <ul class="small">
                  {taken.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
                {taken.more > 0 && <p class="small muted">{w.more(taken.more)}</p>}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  const id = `rule-update-${encodeURIComponent(`${campaignRef}-${name}`)}`;
  // The picker's id names the campaign and the set, since two sets can be followed at once (#4884).
  const pickId = `rule-pick-${encodeURIComponent(`${campaignRef}-${name}`)}`;
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
          <button
            ref={pickButton}
            type="button"
            class="button secondary"
            aria-expanded={picking}
            aria-controls={picking ? pickId : undefined}
            disabled={!api.ruleUpdatePick}
            onClick={() => setPicking((was) => !was)}
          >
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
          props={{
            api,
            campaignRef,
            update,
            id: pickId,
            onPicked: picked,
            onCancel: () => {
              setPicking(false);
              pickButton.current?.focus();
            },
            onError: pickFailed,
          }}
        />
      )}
    </div>
  );
}

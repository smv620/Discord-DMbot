/** @jsxImportSource preact */
// "🔗 Look-alikes" (#5259): each group of names DMbot thinks may be one name, answered here as Discord's panel does it: tick the names
// that are the same, pick the one to keep, then 🔗 Merge (one confirmation naming them all) or None are the same. The result line
// carries the one Undo. DM only: the door answers anyone else with nothing. Its own file, loaded by the Questions page and the Names page
// while LOOKALIKES_LIVE. Every answer is queued for the bot, so each wait shows its stage, how long it has run, and that it is still going.
import { useEffect, useRef, useState } from "preact/hooks";

import { lookAlikeWords as w } from "../content/lookalikes-words";
import { ApiError, type AccountApi, type LookAlikeName } from "./api";
import SavingWait from "./SavingWait";

/** How many groups show at once (Discord shows one; five keep the page short, like the other lists). */
const SHOWN = 5;

/** The plain line for an answer that did not go through, by the error the API mapped. */
function sayFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "undo-late") return w.tooLate;
    if (error.kind === "changed" || error.kind === "gone") return w.changed;
    if (error.kind === "only-dm" || error.kind === "not-allowed" || error.kind === "not-found") return w.notAllowed;
  }
  return w.couldNot;
}

interface View {
  ticked: Set<string>;
  keep: string;
  confirming: boolean;
}
const keyOf = (g: LookAlikeName[]): string => g.map((n) => n.id).join(",");
const fresh = (g: LookAlikeName[]): View => ({ ticked: new Set(g.map((n) => n.id)), keep: g[0]?.id ?? "", confirming: false });

export default function LookAlikes({
  api,
  campaignRef,
  id,
  onLeft,
  heading = true,
}: {
  api: AccountApi;
  campaignRef: string;
  id?: string;
  onLeft?: (left: number) => void;
  /** The Names page puts its own "🔗 Look-alikes (N)" heading; the Questions page's kind line already says it. */
  heading?: boolean;
}) {
  const [groups, setGroups] = useState<LookAlikeName[][] | null>(null);
  const [failed, setFailed] = useState(false);
  const [views, setViews] = useState<Record<string, View>>({});
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string; undo?: { batch: number; count: number; keep: string } } | null>(null);
  const [problem, setProblem] = useState<{ group: string; text: string } | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.lookAlikes) return;
    try {
      const got = await api.lookAlikes(campaignRef);
      if (!live.current) return;
      setGroups(got.groups);
      setFailed(false);
      onLeft?.(got.groups.length);
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  const view = (g: LookAlikeName[]): View => views[keyOf(g)] ?? fresh(g);
  const change = (g: LookAlikeName[], next: Partial<View>): void => setViews((was) => ({ ...was, [keyOf(g)]: { ...view(g), ...next } }));

  // One answer in flight at a time; the groups are read again after each, so what shows is always the API's own.
  async function press(run: () => Promise<void>): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await read();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  function tryMerge(g: LookAlikeName[]): void {
    const v = view(g);
    setProblem(null);
    if (v.ticked.size < 2) return setProblem({ group: keyOf(g), text: w.oneTicked });
    if (!v.ticked.has(v.keep)) return setProblem({ group: keyOf(g), text: w.keepUnticked });
    change(g, { confirming: true });
  }
  const merge = (g: LookAlikeName[]) =>
    press(async () => {
      const v = view(g);
      const others = g.filter((n) => v.ticked.has(n.id) && n.id !== v.keep);
      const keep = g.find((n) => n.id === v.keep);
      if (!api.mergeLookAlikes || !keep) return;
      const got = await api.mergeLookAlikes(campaignRef, { keep: keep.id, others: others.map((n) => n.id), apart: g.filter((n) => !v.ticked.has(n.id)).map((n) => n.id) });
      if (live.current) {
        const text = `${w.done(others.length, keep.name)}${got.secret ? ` ${got.secret}` : ""}`;
        setSaid({ ok: true, text, ...(got.batch !== null ? { undo: { batch: got.batch, count: others.length, keep: keep.name } } : {}) });
      }
    });
  const apart = (g: LookAlikeName[]) =>
    press(async () => {
      await api.lookAlikesApart?.(campaignRef, g.map((n) => n.id));
      if (live.current) setSaid({ ok: true, text: w.noted });
    });
  const undo = (batch: number) =>
    press(async () => {
      await api.undoLookAlikes?.(campaignRef, batch);
      if (live.current) setSaid({ ok: true, text: w.undone });
    });

  if (!api.lookAlikes) return null;
  if (failed && !groups) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">
          {w.failed}
        </p>
      </div>
    );
  }
  if (!groups) return <p class="muted" role="status">{w.loading}</p>;

  const shown = groups.slice(0, SHOWN);
  return (
    <section id={id} class="stack" aria-labelledby={heading ? `${id ?? "lookalikes"}-heading` : undefined} aria-label={heading ? undefined : w.title}>
      {heading && <h3 id={`${id ?? "lookalikes"}-heading`}>{w.heading(groups.length)}</h3>}
      <div role="status" aria-live="polite">
        {said?.ok && <p class="ok">{said.text}</p>}
        {said?.ok && said.undo && (
          <p class="small">
            {w.undoHint(said.undo.count, said.undo.keep)}{" "}
            <button type="button" class="button secondary" disabled={busy} onClick={() => void undo(said.undo!.batch)}>
              {w.undo}
            </button>
          </p>
        )}
      </div>
      {said && !said.ok && (
        <p class="warn" role="alert">
          {said.text}
        </p>
      )}
      <SavingWait active={busy} stage={w.stage} whatToDo={w.slow} />
      {groups.length === 0 && <p class="small muted">{w.none}</p>}
      {shown.map((g, i) => {
        const v = view(g);
        const key = keyOf(g);
        const keepName = g.find((n) => n.id === v.keep);
        const others = g.filter((n) => v.ticked.has(n.id) && n.id !== v.keep);
        return (
          <fieldset key={key} class="panel stack" disabled={busy}>
            <legend>{w.question(i + 1, groups.length)}</legend>
            <p class="small muted">{w.explain}</p>
            <div class="stack" role="group" aria-label={w.tickLabel}>
              {g.map((n) => (
                <label key={n.id} class="choice">
                  <input
                    type="checkbox"
                    checked={v.ticked.has(n.id)}
                    onChange={() => {
                      const next = new Set(v.ticked);
                      if (!next.delete(n.id)) next.add(n.id);
                      change(g, { ticked: next, confirming: false });
                    }}
                  />
                  <span>{w.line(n.name, n.kind, n.heard)}</span>
                </label>
              ))}
            </div>
            <div class="row">
              <label for={`${key}-keep`}>{w.keepLabel}</label>
              <select id={`${key}-keep`} value={v.keep} onChange={(e) => change(g, { keep: (e.target as HTMLSelectElement).value, confirming: false })}>
                {g.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </div>
            {problem?.group === key && (
              <p class="warn small" role="alert">
                {problem.text}
              </p>
            )}
            {v.confirming && keepName ? (
              <div class="stack" role="group" aria-label={w.merge}>
                <p>{w.confirm(others, keepName)}</p>
                <div class="row">
                  <button type="button" class="button" onClick={() => void merge(g)}>
                    {w.yes}
                  </button>
                  <button type="button" class="button secondary" onClick={() => change(g, { confirming: false })}>
                    {w.no}
                  </button>
                </div>
              </div>
            ) : (
              <div class="row">
                <button type="button" class="button" aria-label={keepName ? w.mergeFor(keepName.name) : w.merge} onClick={() => tryMerge(g)}>
                  {w.merge}
                </button>
                <button type="button" class="button secondary" onClick={() => void apart(g)}>
                  {w.apart}
                </button>
              </div>
            )}
          </fieldset>
        );
      })}
      {groups.length > SHOWN && <p class="small muted">{w.more(groups.length - SHOWN)}</p>}
    </section>
  );
}

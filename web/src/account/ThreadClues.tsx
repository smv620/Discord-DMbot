/** @jsxImportSource preact */
// The clues of a story thread on the quest sheet (#4625): the count in Discord's words, each clue found or not and false or not, and the
// DM's own changes (add a clue, mark or unmark a false lead, take one off), with Undo after a change. One change at a time. DM only: a
// refused read shows nothing at all. No id is shown.
import { useEffect, useRef, useState } from "preact/hooks";

import { threadClueWords as w } from "../content/thread-clues-words";
import { ApiError, type AccountApi, type ThreadClues, type ThreadCluesChange } from "./api";

/** The plain line for a change that did not go through. */
function sayFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return w.failed;
  if (error.kind === "not-found") return w.missing;
  if (error.kind === "bad-action") return w.badAction;
  return w.failed;
}

export default function ThreadClues({ api, refId, thread }: { api: AccountApi; refId: string; thread: string }) {
  const [data, setData] = useState<ThreadClues | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [undo, setUndo] = useState<number | null>(null);
  const flight = useRef(false);

  async function load(): Promise<void> {
    if (!api.threadClues) return;
    try {
      setData(await api.threadClues(refId, thread));
    } catch {
      setData(null); // a refused or failed read shows nothing at all
    }
  }
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId, thread]);

  async function change(c: ThreadCluesChange, done: string, after?: () => void): Promise<void> {
    if (flight.current || !api.changeThreadClues) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    setUndo(null);
    try {
      const made = await api.changeThreadClues(refId, thread, c);
      setSaid({ ok: true, text: done });
      if (made.batch !== null) setUndo(made.batch);
      after?.();
    } catch (error) {
      if (error instanceof ApiError && error.kind === "not-allowed") setData(null); // a player's sheet shows no clues at all
      else setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await load(); // (what shows is always the API's own)
      flight.current = false;
      setBusy(false);
    }
  }

  if (!data) return null;
  const add = name.trim();
  return (
    <section class="stack" aria-labelledby={`clues-${thread}`}>
      <h3 id={`clues-${thread}`}>{w.heading}</h3>
      {data.count !== "" && <p class="small">{data.count}</p>}
      {data.clues.length === 0 ? (
        <p class="muted">{w.empty}</p>
      ) : (
        <ul class="stack">
          {data.clues.map((c) => (
            <li key={c.id}>
              <p>
                <strong>{c.name}</strong> · {c.found ? w.found : w.notFound}
                {c.falseLead ? ` · ${w.falseLead}` : ""}
              </p>
              <div class="row">
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  onClick={() => void change({ action: "word", clue: c.id, falseLead: !c.falseLead }, c.falseLead ? w.falseLeadUndone : w.falseLeadDone)}
                >
                  {c.falseLead ? w.falseLeadOff : w.falseLeadOn}
                </button>
                <button type="button" class="button secondary" disabled={busy} onClick={() => void change({ action: "off", clue: c.id }, w.offDone)}>
                  {w.off}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form
        class="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (add === "" || busy) return;
          void change({ action: "add", name: add }, w.added(add), () => setName(""));
        }}
      >
        <label>
          {w.addLabel}
          <input type="text" maxLength={100} value={name} disabled={busy} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
        </label>
        <button type="submit" class="button" disabled={busy || add === ""}>
          {w.add}
        </button>
      </form>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">{busy ? w.saving : said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>
      {undo !== null && !busy && (
        <div class="row">
          <button
            type="button"
            class="button secondary"
            onClick={() => void change({ action: "undo", batch: undo }, w.undone, () => setUndo(null))}
          >
            {w.undo}
          </button>
        </div>
      )}
    </section>
  );
}

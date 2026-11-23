/** @jsxImportSource preact */
// The story threads card on the Names page (#4482, the website half of #4457): DMbot's guesses of quests and hooks in an upload, for the DM
// to keep (with the people and the lines ticked), rename or turn down, with Skip and Undo. Level with Discord's 📜 Story threads card. Its own
// file, fetched only for a DM while THREADS_LIVE. Every press is one change for the bot; the list is read again after each. No id is shown.
import { useEffect, useRef, useState } from "preact/hooks";

import { storyThreadsWords as w } from "../content/threads-words";
import { ApiError, type AccountApi, type StoryThreadSuggestion, type StoryThreads, type StoryThreadsChange } from "./api";

type Said = { ok: boolean; text: string };
// The last press that can be taken back: a keep (with its batch) or a turn-down (with its id).
type Undoable = { kind: "keep"; batch: number; name: string } | { kind: "not"; id: string; name: string } | null;
// Which people and lines the DM has un-ticked for a suggestion (none un-ticked means all are kept).
type Unticked = { people: number[]; lines: number[] };

export default function StoryThreads({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [list, setList] = useState<StoryThreads | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [shown, setShown] = useState(0);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const [undoable, setUndoable] = useState<Undoable>(null);
  const [unticked, setUnticked] = useState<Record<string, Unticked>>({});
  const [renamed, setRenamed] = useState<Record<string, string>>({});
  const [renaming, setRenaming] = useState(false);
  const [renameText, setRenameText] = useState("");
  const flight = useRef(false);

  async function load(): Promise<void> {
    try {
      setList(await api.storyThreads!(refId));
      setProblem(null);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      setProblem(error instanceof ApiError && error.kind === "not-allowed" ? w.notDm : w.failed);
    }
  }
  useEffect(() => {
    void load();
  }, [api, refId]);

  async function press(change: StoryThreadsChange, then: (made: { batch: number | null }) => Said, next: Undoable): Promise<void> {
    if (flight.current || !api.changeStoryThreads) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    setRenaming(false);
    try {
      const made = await api.changeStoryThreads(refId, change);
      setSaid(then(made));
      setUndoable(next && (next.kind === "not" || made.batch !== null) ? (next.kind === "keep" ? { kind: "keep", batch: made.batch!, name: next.name } : next) : null);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      const kind = error instanceof ApiError ? error.kind : "";
      const text = kind === "gone" ? w.gone : kind === "undo-late" ? w.tooLate : w.wrong;
      setSaid({ ok: false, text });
      // A press that failed takes back nothing: the Undo of the earlier press is no longer offered.
      setUndoable(null);
      if (kind === "gone") setShown(0);
    } finally {
      flight.current = false;
      setBusy(false);
      await load();
    }
  }

  if (problem) {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p role="alert">{problem}</p>
        {problem === w.failed && (
          <button type="button" class="button secondary" onClick={() => void load()}>
            {w.tryAgain}
          </button>
        )}
      </section>
    );
  }
  if (!list) {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p class="muted" role="status">{w.loading}</p>
      </section>
    );
  }
  const suggestions = list.suggestions;
  const index = suggestions.length === 0 ? 0 : shown % suggestions.length;
  const s: StoryThreadSuggestion | undefined = suggestions[index];
  const name = (t: StoryThreadSuggestion): string => renamed[t.id] ?? t.name;
  const ticks = (t: StoryThreadSuggestion): Unticked => unticked[t.id] ?? { people: [], lines: [] };
  const toggle = (t: StoryThreadSuggestion, field: keyof Unticked, value: number): void => {
    const cur = ticks(t);
    const list2 = cur[field].includes(value) ? cur[field].filter((v) => v !== value) : [...cur[field], value];
    setUnticked({ ...unticked, [t.id]: { ...cur, [field]: list2 } });
  };

  const keep = (t: StoryThreadSuggestion): void => {
    const off = ticks(t);
    const on = t.people.map((p) => p.index).filter((i) => !off.people.includes(i));
    const lines = t.lines.map((_, i) => i).filter((i) => !off.lines.includes(i));
    const newName = renamed[t.id];
    const change: StoryThreadsChange = { action: "keep", id: t.id, on, lines };
    if (newName !== undefined && newName !== t.name) change.name = newName;
    void press(change, () => ({ ok: true, text: w.kept(name(t)) }), { kind: "keep", batch: 0, name: name(t) });
  };
  const turnDown = (t: StoryThreadSuggestion): void => {
    void press({ action: "not", id: t.id }, () => ({ ok: true, text: w.turnedDown(name(t)) }), { kind: "not", id: t.id, name: name(t) });
  };
  const undo = (u: Exclude<Undoable, null>): void => {
    const change: StoryThreadsChange = u.kind === "keep" ? { action: "undo", keep: u.batch } : { action: "undo", not: u.id };
    void press(change, () => ({ ok: true, text: w.undone(u.name) }), null);
  };

  return (
    <section class="stack" aria-label={w.heading} aria-busy={busy}>
      <h2 class="small">{w.heading}</h2>
      <p class="small">{w.intro}</p>
      {busy && <p class="small muted" role="status">{w.working}</p>}
      {!s && <p class="muted">{w.none}</p>}
      {s && (
        <div class="stack">
          <h3 class="small">{s.session ? w.heardHeading : w.cardHeading}</h3>
          <p>
            <strong>{name(s)}</strong> · {s.kind === "quest" ? w.questKind : w.hookKind}
          </p>
          <p class="small">{w.aim(s.aim)}</p>
          {s.people.length > 0 && (
            <>
              <h3 class="small">{w.involved}</h3>
              <ul class="stack small">
                {s.people.map((p) => (
                  <li key={p.index}>
                    <label class="choice">
                      <input type="checkbox" checked={!ticks(s).people.includes(p.index)} disabled={busy} onChange={() => toggle(s, "people", p.index)} />
                      {`${p.name} · ${p.role}`}
                    </label>
                  </li>
                ))}
              </ul>
              <p class="small muted">{w.untickPeople}</p>
            </>
          )}
          {s.lines.length > 0 && (
            <>
              <h3 class="small">{w.whatsIn}</h3>
              <ul class="stack small">
                {s.lines.map((l, i) => (
                  <li key={i}>
                    <label class="choice">
                      <input type="checkbox" checked={!ticks(s).lines.includes(i)} disabled={busy} onChange={() => toggle(s, "lines", i)} />
                      {`${l.words} · ${l.what}`}
                    </label>
                  </li>
                ))}
              </ul>
              <p class="small muted">{w.untickLines}</p>
            </>
          )}
          {s.session ? (
            // A session suggestion says where it was heard, with its lines as text in one quote (#4526).
            s.heard.length > 0 ? (
              <>
                <p class="small">{`${s.upload !== "" ? w.heardIn(s.upload) : w.heardInSession}:`}</p>
                <blockquote class="small">{s.heard.join(" … ")}</blockquote>
              </>
            ) : (
              <p class="small">{`${s.upload !== "" ? w.heardIn(s.upload) : w.heardInSession}.`}</p>
            )
          ) : (
            s.quote !== "" && (
              <>
                <p class="small">{w.from(s.upload)}</p>
                <blockquote class="small">{s.quote}</blockquote>
              </>
            )
          )}
          <p class="small muted">{w.keepHint}</p>
          {renaming && (
            <p class="row">
              <label class="field">
                {w.renameField}
                <input type="text" maxLength={100} value={renameText} onInput={(e) => setRenameText((e.target as HTMLInputElement).value)} />
              </label>
              <button
                type="button"
                class="button secondary"
                disabled={busy || renameText.trim() === ""}
                onClick={() => {
                  setRenamed({ ...renamed, [s.id]: renameText.trim() });
                  setRenaming(false);
                }}
              >
                {w.renameDo}
              </button>
              <button type="button" class="link" onClick={() => setRenaming(false)}>{w.cancel}</button>
            </p>
          )}
          <p class="row">
            <button type="button" class="button" disabled={busy} onClick={() => keep(s)}>{w.keepIt}</button>
            <button
              type="button"
              class="button secondary"
              disabled={busy}
              onClick={() => {
                setRenameText(name(s));
                setRenaming(true);
              }}
            >
              {w.rename}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => turnDown(s)}>{w.notAStory}</button>
            {suggestions.length > 1 && (
              <button type="button" class="button secondary" disabled={busy} onClick={() => setShown(index + 1)}>{w.skip}</button>
            )}
          </p>
        </div>
      )}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {undoable !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => undo(undoable)}>{w.undo}</button>
          )}
        </p>
      )}
    </section>
  );
}

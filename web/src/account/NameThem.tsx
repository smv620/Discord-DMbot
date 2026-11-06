/** @jsxImportSource preact */
// "Give them a name" for a person the story never named (#2803; core web/all_names.py, actions name and name-join). The
// game master types a name; the bot applies it. If DMbot already knows someone with that name it is not renamed: the
// page asks whether they are the same person, and only "Yes" joins the two. Every step waits for the bot and shows
// what it is doing, how long it has run and when it last moved (#2564). One press at a time: a second press while a
// step is on its way is ignored. Shown only for a DM, only while NOT_NAMED_LIVE (the Names page decides).
import { useEffect, useRef, useState } from "preact/hooks";

import { NAME_LIMIT } from "../content/names-words";
import { REMOVE_UNNAMED_LIVE } from "../content/remove-unnamed-switch";
import { notNamedWords as w } from "../content/not-named-words";
import { ApiError, type AccountApi, type NameChange, type NameRow, type NameThemResult } from "./api";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

export interface NamedInfo {
  text: string;
  /** The id that takes the step back, or null when it can't be taken back. */
  undo: string | null;
  /** True after a join: the Undo then says so. */
  join: boolean;
}

const problem = (kind: string): string => (Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));

export default function NameThem({
  id,
  row,
  api,
  refId,
  act,
  onDone,
  onSignedOut,
}: {
  id: string;
  row: NameRow;
  api: AccountApi;
  refId: string;
  /** The page's own save for a name change (reads the list again, says what happened): used to remove the person. */
  act: (change: NameChange, ok: string) => Promise<string | null>;
  onDone: (info: NamedInfo) => Promise<void>;
  onSignedOut: () => void;
}) {
  const [name, setName] = useState("");
  const [working, setWorking] = useState(false);
  const [looks, setLooks] = useState(0);
  const [question, setQuestion] = useState<{ id: string; name: string } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [joining, setJoining] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [asking, setAsking] = useState(false); // "Remove this person?", asked once
  const [removing, setRemoving] = useState(false);
  // "Same as someone DMbot knows…": a search of the names DMbot knows; picking one asks the same question as typing it (#3597).
  const [finding, setFinding] = useState(false);
  const [find, setFind] = useState("");
  const [matches, setMatches] = useState<{ id: string; name: string }[] | null>(null);
  const [findFailed, setFindFailed] = useState(false);
  const findBox = useRef<HTMLInputElement>(null);
  const askBox = useRef<HTMLParagraphElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const questionBox = useRef<HTMLParagraphElement>(null);
  const asked = useRef("");
  const flight = useRef(false);
  const seen = useObservedWait(working, String(looks));

  async function run(step: Parameters<NonNullable<AccountApi["nameThem"]>>[2], text: (given: string) => string): Promise<void> {
    if (flight.current || !api.nameThem) return;
    flight.current = true;
    setWorking(true);
    setJoining(step.action === "name-join");
    setDeclined(false);
    setReport(null);
    setLooks(0);
    let done: NamedInfo | null = null;
    try {
      const got: NameThemResult = await api.nameThem(refId, row.id, step, () => setLooks((n) => n + 1));
      if (got.state === "question") {
        setQuestion(got.other);
        setTimeout(() => questionBox.current?.focus(), 0);
        return;
      }
      if (got.state === "player") return; // (only a Discord ID lookup says this: not asked here)
      setQuestion(null);
      done = { text: text(asked.current), undo: got.undo, join: step.action === "name-join" };
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") return onSignedOut();
      setReport(problem(kind));
    } finally {
      flight.current = false;
      setWorking(false);
    }
    // After the try: the name is already applied, so a list that fails to reload must not read as a failed change.
    if (done) await onDone(done).catch(() => undefined);
  }

  // The search waits for a pause in the typing and asks for the first few names; a late answer to an old word is dropped.
  useEffect(() => {
    const wanted = find.trim();
    if (!finding || wanted === "" || !api.allNames) {
      setMatches(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      api
        .allNames?.(refId, { q: wanted, kind: "", sort: "name", page: 1, size: 20 })
        .then((got) => {
          if (!live) return;
          setFindFailed(false);
          setMatches(got.names.filter((n) => !n.unnamed && n.id !== row.id).slice(0, 8).map((n) => ({ id: n.id, name: n.name })));
        })
        .catch(() => live && setFindFailed(true));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [find, finding, api, refId, row.id]);

  async function remove(): Promise<void> {
    if (working || removing) return;
    setRemoving(true);
    setReport(null);
    const problem = await act({ action: "remove", confirm: true }, w.removed(row.name));
    setRemoving(false);
    if (problem !== null && problem !== "") {
      setAsking(false);
      setReport(problem);
    }
  }

  const submit = (e: Event): void => {
    e.preventDefault();
    const clean = name.trim().replace(/\s+/g, " ");
    if (working) return;
    if (clean === "") return setReport(w.nameFirst);
    asked.current = clean;
    setQuestion(null);
    void run({ action: "name", name: clean }, (given) => w.named(row.name, given));
  };

  return (
    <div class="stack name-them" id={id}>
      <p class="small muted">{w.hint}</p>
      {/* A short who, where and what from the story, when the API has one (#3596): room is kept for it. */}
      {row.scene && <p class="small">{row.scene}</p>}
      {question ? (
        <div class="stack" role="group" aria-label={w.known(row.name, question.name)}>
          <p role="status" tabIndex={-1} ref={questionBox}>
            {w.known(row.name, question.name)}
          </p>
          <div class="row">
            <button
              type="button"
              class="button"
              aria-disabled={working}
              onClick={() => !working && void run({ action: "name-join", into: question.id }, () => w.joined(row.name, question.name))}
            >
              {w.sameYes}
            </button>
            <button type="button" class="button secondary" aria-disabled={working} onClick={() => {
                if (working) return;
                setQuestion(null);
                setDeclined(true);
                setName("");
              }}>
              {w.sameNo}
            </button>
          </div>
        </div>
      ) : (
        <form class="stack" onSubmit={submit}>
          {declined && <p class="small">{w.sameNoNote(row.name)}</p>}
          <label>
            {w.nameLabel}
            <input
              type="text"
              value={name}
              maxLength={NAME_LIMIT}
              autoComplete="off"
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
          </label>
          <p>
            <button type="submit" class="button" aria-disabled={working}>
              {w.give}
            </button>
          </p>
        </form>
      )}
      {!question && (
        <div class="stack">
          {finding ? (
            <div class="stack">
              <label class="field" for={`${id}-find`}>
                {w.findLabel}
              </label>
              <input id={`${id}-find`} ref={findBox} type="text" value={find} autoComplete="off" aria-describedby={`${id}-find-hint`} onInput={(e) => setFind((e.target as HTMLInputElement).value)} />
              <p class="small muted" id={`${id}-find-hint`}>
                {w.findHint}
              </p>
              {matches !== null && matches.length > 0 && (
                <ul class="stack plain-list">
                  {matches.map((m) => (
                    <li key={m.id}>
                      <button
                        type="button"
                        class="button secondary"
                        aria-label={w.pickFor(m.name)}
                        aria-disabled={working}
                        onClick={() => {
                          if (working) return;
                          setQuestion({ id: m.id, name: m.name });
                          setFinding(false);
                          setTimeout(() => questionBox.current?.focus(), 0);
                        }}
                      >
                        {m.name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p class="small" role="status">
                {findFailed ? w.findFailed : matches !== null && matches.length === 0 ? w.findNone : ""}
              </p>
              <p>
                <button type="button" class="button secondary" onClick={() => { setFinding(false); setFind(""); setMatches(null); }}>
                  {w.findClose}
                </button>
              </p>
            </div>
          ) : (
            <p>
              <button type="button" class="button secondary" aria-disabled={working} onClick={() => { if (working) return; setFinding(true); setTimeout(() => findBox.current?.focus(), 0); }}>
                {w.samePick}
              </button>
            </p>
          )}
        </div>
      )}
      {/* Always there (a screen reader hears what is put in it), but drawn only when it says something. */}
      <p class={report ? "warn small" : "visually-hidden"} role="alert">
        {report ?? ""}
      </p>
      {REMOVE_UNNAMED_LIVE && (
      <div class="stack">
        {asking ? (
          <div class="stack" role="group" aria-label={w.removeAsk(row.name)}>
            <p role="status" tabIndex={-1} ref={askBox}>
              {w.removeAsk(row.name)}
            </p>
            <div class="row">
              <button type="button" class="button secondary" aria-disabled={working || removing} onClick={() => void remove()}>
                {w.removeYes}
              </button>
              <button type="button" class="button secondary" aria-disabled={removing} onClick={() => {
                  if (removing) return;
                  setAsking(false);
                  setTimeout(() => opener.current?.focus(), 0); // the question goes: the keyboard returns to the button that opened it
                }}>
                {w.removeKeep}
              </button>
            </div>
          </div>
        ) : (
          <p>
            <button type="button" class="button secondary" aria-disabled={working || removing} ref={opener} onClick={() => {
                if (working || removing) return;
                setAsking(true);
                setTimeout(() => askBox.current?.focus(), 0);
              }}>
              {w.removeButton}
            </button>
          </p>
        )}
      </div>
      )}
      {working && (
        <WorkProgress
          stage={looks === 0 ? (w.stage["waiting"] as string) : (w.stage[joining ? "joining" : "working"] as string)}
          startedAt={seen.startedAt}
          updatedAt={seen.movedAt}
          state={seen.quiet ? "stalled" : "running"}
          progressUnknown
          whatToDo={w.quiet}
        />
      )}
    </div>
  );
}

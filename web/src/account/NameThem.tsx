/** @jsxImportSource preact */
// "Give them a name" for a person the story never named (#2803; core web/all_names.py, actions name and name-join). The
// game master types a name; the bot applies it. If DMbot already knows someone with that name it is not renamed: the
// page asks whether they are the same person, and only "Yes" joins the two. Every step waits for the bot and shows
// what it is doing, how long it has run and when it last moved (#2564). One press at a time: a second press while a
// step is on its way is ignored. Shown only for a DM, only while NOT_NAMED_LIVE (the Names page decides).
import { useRef, useState } from "preact/hooks";

import { NAME_LIMIT } from "../content/names-words";
import { notNamedWords as w } from "../content/not-named-words";
import { ApiError, type AccountApi, type NameRow, type NameThemResult } from "./api";
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
  onDone,
  onSignedOut,
}: {
  id: string;
  row: NameRow;
  api: AccountApi;
  refId: string;
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
      <p class="warn small" role="alert">
        {report ?? ""}
      </p>
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

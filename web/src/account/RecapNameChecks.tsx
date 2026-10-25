/** @jsxImportSource preact */
// "Names to check" on a recap's page (#2114 part 3; core #2113): the new names DMbot found in the
// recap and is not sure of. The DM says what each one is: right (and what kind), the same as a name
// already known, spelled another way, not a name at all, or explained in a few words. An answer only
// queues for the bot (the same paths as Discord's 📝 Check new names), so this page asks again about
// every two seconds until the name leaves the list. DMbot never decides what a name is.
// Shown only while RECAPS_LIVE and RECAP_NAME_CHECKS_LIVE are true, for the campaign's DM.
import { useEffect, useRef, useState } from "preact/hooks";

import { NOTE_MAX_LETTERS, SPELLING_MAX_LETTERS, nameCheckWords as words } from "../content/recap-name-checks";
import { RECAP_NAME_CHECKS_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type NameCheck, type NameCheckAnswer, type NameCheckList } from "./api";
import { keepFocus } from "./focus";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

const POLL_MS = 2000;
const POLL_LIMIT = 30; // about a minute: the bot normally answers within seconds

export default function RecapNameChecks({ api, refId }: { api: AccountApi; refId: string }) {
  const [list, setList] = useState<NameCheckList | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [slow, setSlow] = useState(false);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [said, setSaid] = useState("");
  const looks = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);

  const read = async (): Promise<NameCheckList | null> => {
    if (!api.nameChecks) return null;
    try {
      const got = await api.nameChecks(refId);
      setList(got);
      setFailed(false);
      return got;
    } catch {
      setFailed(list === null);
      return null;
    }
  };

  useEffect(() => {
    if (!RECAPS_LIVE || !RECAP_NAME_CHECKS_LIVE) return;
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId]);

  const waiting = (list?.names ?? []).some((n) => n.answering) || busy.size > 0;
  // The wait shows how long it has run and when the list last changed (the page sees no stage from the bot).
  const seen = useObservedWait(waiting, (list?.names ?? []).map((n) => `${n.id}:${n.answering ? 1 : 0}`).join(","));
  useEffect(() => {
    if (!waiting) {
      looks.current = 0;
      setSlow(false);
      return;
    }
    if (slow) return; // gave up after about a minute; a new answer starts the watching again
    const timer = setTimeout(async () => {
      looks.current += 1;
      const before = new Set((list?.names ?? []).map((n) => n.id));
      const got = await read();
      if (got) {
        const now = new Set(got.names.map((n) => n.id));
        // A name that left the list is answered; one that is still there with a word in `error` failed.
        setBusy((was) => {
          const next = new Set(was);
          for (const id of was) if (!now.has(id) || (got.names.find((n) => n.id === id)?.error ?? "") !== "") next.delete(id);
          return next;
        });
        if ([...before].some((id) => !now.has(id))) {
          setSaid(words.done);
          keepFocus(() => heading.current);
        }
      }
      if (looks.current >= POLL_LIMIT) setSlow(true);
    }, POLL_MS);
    return () => clearTimeout(timer);
  });

  if (!RECAPS_LIVE || !RECAP_NAME_CHECKS_LIVE || !api.nameChecks || !api.answerNameCheck) return null;
  if (failed) {
    return (
      <section class="stack" aria-labelledby="name-checks-heading">
        <h3 id="name-checks-heading">{words.heading}</h3>
        <p class="warn" role="alert">
          {words.loadFailed}
        </p>
      </section>
    );
  }
  if (list === null || list.names.length === 0) {
    // Nothing to check; once the last one is answered, the page says so for a moment, then is quiet.
    return said === "" ? null : (
      <p class="small muted" role="status">
        {words.allDone}
      </p>
    );
  }

  async function send(name: NameCheck, answer: NameCheckAnswer) {
    setProblems((was) => ({ ...was, [name.id]: "" }));
    setBusy((was) => new Set(was).add(name.id));
    setSlow(false);
    looks.current = 0;
    try {
      await api.answerNameCheck!(refId, name.id, answer);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblems((was) => ({ ...was, [name.id]: words.problem[kind] ?? words.problem.server ?? "" }));
      setBusy((was) => {
        const next = new Set(was);
        next.delete(name.id);
        return next;
      });
    }
  }

  return (
    <section class="stack" aria-labelledby="name-checks-heading">
      <h3 id="name-checks-heading" tabIndex={-1} ref={heading}>
        {words.heading}
      </h3>
      <p class="muted">{words.lead}</p>
      <ul class="name-checks">
        {list.names.map((n) => (
          <NameRow
            key={n.id}
            name={n}
            list={list}
            busy={busy.has(n.id) || n.answering}
            problem={problems[n.id] || (n.error ? words.problem[n.error.replaceAll("_", "-")] ?? words.failedHint : "")}
            onSend={(answer) => void send(n, answer)}
          />
        ))}
      </ul>
      {/* Always mounted, so a screen reader hears the stage and then the result. */}
      <p class="sr-only" role="status">
        {waiting ? (slow || seen.quiet ? "" : words.waiting) : said}
      </p>
      {waiting && seen.startedAt !== null ? (
        <WorkProgress
          stage={words.waiting}
          startedAt={seen.startedAt}
          updatedAt={seen.movedAt}
          state={slow || seen.quiet ? "stalled" : "running"}
          {...(slow ? { whatToDo: words.slow } : {})}
        />
      ) : (
        <p class="small muted" aria-hidden="true">
          {said}
        </p>
      )}
    </section>
  );
}

function NameRow({
  name,
  list,
  busy,
  problem,
  onSend,
}: {
  name: NameCheck;
  list: NameCheckList;
  busy: boolean;
  problem: string;
  onSend: (answer: NameCheckAnswer) => void;
}) {
  const id = `name-check-${name.id}`;
  // No kind is chosen for the DM: a tap on Yes with a kind nobody picked would be a wrong answer.
  const [kind, setKind] = useState("");
  const [known, setKnown] = useState("");
  const [spelling, setSpelling] = useState("");
  const [note, setNote] = useState("");
  const room = NOTE_MAX_LETTERS - note.length;
  return (
    <li>
      <div class="panel stack" role="group" aria-labelledby={`${id}-name`}>
        <strong id={`${id}-name`}>{name.name}</strong>
        {name.note && (
          <p class="small muted">
            {words.noteFrom} {name.note}
          </p>
        )}
        <div class="row">
          <label for={`${id}-kind`}>{words.kind}</label>
          <select id={`${id}-kind`} value={kind} disabled={busy} onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
            <option value="">{words.kindNone}</option>
            {list.kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
          <button type="button" class="button" disabled={busy || kind === ""} aria-label={words.yesWith(name.name)} onClick={() => onSend({ action: "confirm", kind })}>
            {words.yes}
          </button>
          <button type="button" class="button secondary" disabled={busy} aria-label={words.notANameFor(name.name)} onClick={() => onSend({ action: "not_a_name" })}>
            {words.notAName}
          </button>
        </div>
        <details>
          <summary>{words.more}</summary>
          <fieldset class="stack" disabled={busy}>
            <legend>{words.actually}</legend>
            <div class="row">
              <label for={`${id}-known`}>{words.sameLabel}</label>
              <select id={`${id}-known`} value={known} onChange={(e) => setKnown((e.target as HTMLSelectElement).value)}>
                <option value="">{words.sameNone}</option>
                {list.known.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                  </option>
                ))}
              </select>
              <button type="button" class="button secondary" disabled={known === ""} aria-label={words.sameFor(name.name)} onClick={() => onSend({ action: "same", known })}>
                {words.sameUse}
              </button>
            </div>
            <div class="row">
              <label for={`${id}-spell`}>{words.spellLabel}</label>
              <input id={`${id}-spell`} type="text" maxLength={SPELLING_MAX_LETTERS} autocomplete="off" autocapitalize="words" spellcheck={false} value={spelling} onInput={(e) => setSpelling((e.target as HTMLInputElement).value)} />
              <button type="button" class="button secondary" disabled={spelling.trim() === ""} aria-label={words.spellFor(name.name)} onClick={() => onSend({ action: "respell", text: spelling.trim() })}>
                {words.spellUse}
              </button>
            </div>
          </fieldset>
          <div class="row">
            <label for={`${id}-note`}>{words.explainLabel}</label>
            <input id={`${id}-note`} type="text" maxLength={NOTE_MAX_LETTERS} autocomplete="off" value={note} disabled={busy} onInput={(e) => setNote((e.target as HTMLInputElement).value)} />
            {room <= 50 && <span class="small muted">{words.left(room)}</span>}
            <button type="button" class="button secondary" disabled={busy || note.trim() === ""} aria-label={words.explainFor(name.name)} onClick={() => onSend({ action: "explain", text: note.trim() })}>
              {words.explainSend}
            </button>
          </div>
        </details>
        {problem && (
          <p class="warn small" role="alert">
            {problem}
          </p>
        )}
      </div>
    </li>
  );
}

/** @jsxImportSource preact */
// "Names to check" on a recap's page (#2114 part 3; core #2113): the new names DMbot found in the
// recap and is not sure of. The DM says what each one is: right (and what kind), the same as a name
// already known, spelled another way, not a name at all, or explained in a few words. An answer only
// queues for the bot (the same paths as Discord's 📝 Check new names), so this page asks again about
// every two seconds until the name leaves the list. DMbot never decides what a name is.
// Shown only while RECAPS_LIVE and RECAP_NAME_CHECKS_LIVE are true, for the campaign's DM.
import NamePlaces from "./NamePlaces";
import { campaignRefOf } from "./RecapNames";
import PlayersKnow from "./PlayersKnow";
import SceneLine from "./SceneLine";
import { useEffect, useRef, useState } from "preact/hooks";

import { nameGroupWords as gw } from "../content/name-group-words";
import { NAME_SPLIT_LIVE } from "../content/name-split-switch";
import { NAME_MAX_LETTERS, NOTE_MAX_LETTERS, SPELLING_MAX_LETTERS, nameCheckWords as words } from "../content/recap-name-checks";
import { NAME_CHANGE_WORDS_LIVE, NAME_PLACES_LIVE, RECAP_NAME_CHECKS_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type NameAnswerResult, type NameCheck, type NameCheckAnswer, type NameCheckList } from "./api";
import { keepFocus } from "./focus";
import { currentItem, itemDomId, openItem } from "./itemLink";
import { itemWords } from "../content/item-words";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

const POLL_MS = 2000;
const POLL_LIMIT = 30; // about a minute: the bot normally answers within seconds
const SPLIT_ACTIONS = new Set(["split", "join_group", "make_group"]);
/** The sentence for a refusal word the bot gave for a split, a group or an Undo; "" for any other word. */
const splitReason = (code: string): string => (Object.hasOwn(gw.errors, code) ? gw.reason(code) : "");

/** A name read from a picture carries its own sentence about where it came from ("Read from a picture on page 3 as …", #4003,
 * #4116): it is shown as it is, not under "From the recap:", which would be wrong for it. */
export const noteSaysWhere = (note: string): boolean => /^Read from a picture\b/.test(note);

export default function RecapNameChecks({ api, refId }: { api: AccountApi; refId: string }) {
  const [list, setList] = useState<NameCheckList | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [slow, setSlow] = useState(false);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [said, setSaid] = useState("");
  const looks = useRef(0);
  const changing = useRef(new Set<string>()); // cards whose last answer was Change the words, for an unknown refusal
  const heading = useRef<HTMLHeadingElement>(null);
  // A private message's "Open on the website" names a waiting name (#3663): its card is opened once the list is here, or the
  // page says it has been answered (here or in Discord).
  const wanted = useRef(currentItem());
  const [answered, setAnswered] = useState(false);
  useEffect(() => {
    if (wanted.current === null || list === null) return;
    const item = wanted.current;
    wanted.current = null; // (once: later reads, and the person's own answers, never pull focus back)
    setTimeout(() => {
      if (!openItem(item)) setAnswered(true);
    }, 0);
  }, [list]);
  // Split, group and Undo (#3406): the bot's word on the last such answer, followed until it is done. The name
  // leaves the list once it is applied, so the line with Undo sits under the list, not on the card.
  const splitOn = NAME_SPLIT_LIVE && api.nameAnswerResult !== undefined;
  const [follow, setFollow] = useState<{ id: string; undo: boolean; batch: number } | null>(null);
  const [last, setLast] = useState<{ id: string; note: string; batch: number } | null>(null);
  const [lastProblem, setLastProblem] = useState("");
  const [taken, setTaken] = useState(false); // an Undo finished: say so in plain sight until the next answer

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

  const waiting = (list?.names ?? []).some((n) => n.answering) || busy.size > 0 || follow !== null;
  // The wait shows how long it has run and when the list last changed (the page sees no stage from the bot).
  const seen = useObservedWait(waiting, (list?.names ?? []).map((n) => `${n.id}:${n.answering ? 1 : 0}:${n.name}`).join(","));
  useEffect(() => {
    if (!waiting) {
      looks.current = 0;
      setSlow(false);
      return;
    }
    if (slow) return; // gave up after about a minute; a new answer starts the watching again
    const timer = setTimeout(async () => {
      looks.current += 1;
      const before = new Map((list?.names ?? []).map((n) => [n.id, n.name]));
      const busyNow = new Set(busy);
      const got = await read();
      if (got) {
        const now = new Set(got.names.map((n) => n.id));
        // Change the words keeps the card and gives it new words (#2593), which also ends its wait.
        const renamedCard = got.names.find((n) => busyNow.has(n.id) && before.has(n.id) && before.get(n.id) !== n.name);
        // A name that left the list is answered; one that is still there with a word in `error` failed.
        setBusy((was) => {
          const next = new Set(was);
          for (const id of was) {
            const card = got.names.find((n) => n.id === id);
            if (!card || (card.error ?? "") !== "" || (before.has(id) && before.get(id) !== card.name)) next.delete(id);
          }
          return next;
        });
        if ([...before.keys()].some((id) => !now.has(id))) {
          setSaid(words.done);
          keepFocus(() => heading.current);
        } else if (renamedCard) {
          // The card goes on under its new words: say so, and put the focus where the next answer is made.
          setSaid(words.changed(renamedCard.name));
          keepFocus(() => document.getElementById(`name-check-${renamedCard.id}-kind`));
        }
      }
      if (looks.current >= POLL_LIMIT) setSlow(true);
    }, POLL_MS);
    return () => clearTimeout(timer);
  });

  useEffect(() => {
    if (!follow || !api.nameAnswerResult) return;
    const { id, undo, batch } = follow;
    let stopped = false;
    let looks = 0;
    const end = (after: () => void) => {
      stopped = true;
      setFollow(null);
      after();
      void read(); // the list changed under us (parts added, or taken away again): show it as it is now
    };
    const timer = setInterval(async () => {
      looks += 1;
      try {
        const got: NameAnswerResult = await api.nameAnswerResult!(refId, id);
        if (stopped) return;
        if (got.state === "done" && undo && got.batch === batch) {
          // The split's own row stays "done" until the bot has taken it back, so it is not the Undo's answer.
        } else if (got.state === "done") {
          end(() => {
            if (undo) {
              setLast(null);
              setTaken(true);
              setSaid(gw.undone);
            } else if (got.batch !== null) setLast({ id, note: got.note, batch: got.batch });
          });
        } else if (got.state === "failed") {
          // A refused Undo is said here; a refused split or group shows on the card, from the list.
          end(() => setLastProblem(undo ? gw.reason(got.error) : ""));
        }
      } catch (error) {
        if (stopped) return;
        // No answer on record: an Undo that worked took it with it; any other answer was applied and cleared.
        if (error instanceof ApiError && error.kind === "not-found") {
          end(() => {
            if (undo) {
              setLast(null);
              setTaken(true);
              setSaid(gw.undone);
            }
          });
        }
      }
      if (!stopped && looks >= POLL_LIMIT) end(() => setLastProblem(undo ? gw.undoSlow : ""));
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [follow, api, refId]);

  async function undo() {
    if (!last || !api.answerNameCheck) return;
    setLastProblem("");
    try {
      await api.answerNameCheck(refId, last.id, { action: "undo", batch: last.batch });
      setFollow({ id: last.id, undo: true, batch: last.batch });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setLastProblem(words.problem[kind] ?? words.problem.server ?? "");
    }
  }

  // The line with Undo for the last split or group, and the word on an Undo that was refused.
  const undoLine = (
    <>
      {taken && (
        <p class="small muted">{gw.undone}</p>
      )}
      {splitOn && last && (
        <div class="stack small" role="status">
          <span>{last.note}</span>
          <span>
            <button type="button" class="button secondary" disabled={follow !== null} onClick={() => void undo()}>
              {gw.undo}
            </button>
          </span>
        </div>
      )}
      {lastProblem && (
        <p class="warn small" role="alert">
          {lastProblem}
        </p>
      )}
    </>
  );

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
    // Nothing to check; once the last one is answered, the page says so for a moment, then is quiet. A split or
    // group just made can still be taken back, so its line stays.
    if (said === "" && !last && !taken && !answered) return null;
    return (
      <>
        {answered && (
          <p class="note" role="status">
            {itemWords.answered}
          </p>
        )}
        {said !== "" && !taken && (
          <p class="small muted" role="status">
            {words.allDone}
          </p>
        )}
        {undoLine}
      </>
    );
  }

  async function send(name: NameCheck, answer: NameCheckAnswer) {
    if (answer.action === "change_words") changing.current.add(name.id);
    else changing.current.delete(name.id);
    setProblems((was) => ({ ...was, [name.id]: "" }));
    setLast(null); // (Undo is offered until the next answer)
    setTaken(false);
    if (!SPLIT_ACTIONS.has(answer.action)) setFollow(null); // a plain answer ends any follow: its Undo would be stale
    setLastProblem("");
    setBusy((was) => new Set(was).add(name.id));
    setSlow(false);
    looks.current = 0;
    try {
      await api.answerNameCheck!(refId, name.id, answer);
      if (SPLIT_ACTIONS.has(answer.action)) setFollow({ id: name.id, undo: false, batch: 0 });
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
      {answered && (
        <p class="note" role="status">
          {itemWords.answered}
        </p>
      )}
      <ul class="name-checks">
        {list.names.map((n) => (
          <NameRow
            key={n.id}
            name={n}
            list={list}
            campaignRef={campaignRefOf(refId)}
            busy={busy.has(n.id) || n.answering}
            splitOn={splitOn}
            problem={problems[n.id] || (n.error ? words.problem[n.error.replaceAll("_", "-")] ?? ((splitOn ? splitReason(n.error) : "") || (changing.current.has(n.id) ? words.changeFailed : words.failedHint)) : "")}
            onSend={(answer) => void send(n, answer)}
          />
        ))}
      </ul>
      {undoLine}
      {/* Always mounted, so a screen reader hears the stage and then the result. */}
      <p class="sr-only" role="status">
        {waiting ? (slow || seen.quiet ? "" : follow ? gw.working : words.waiting) : said}
      </p>
      {waiting && seen.startedAt !== null ? (
        <WorkProgress
          stage={follow ? gw.working : words.waiting}
          startedAt={seen.startedAt}
          updatedAt={seen.movedAt}
          state={slow || seen.quiet ? "stalled" : "running"}
          frozen={slow}
          whatToDo={words.slow}
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
  campaignRef,
  busy,
  problem,
  splitOn,
  onSend,
}: {
  name: NameCheck;
  list: NameCheckList;
  campaignRef: string;
  busy: boolean;
  problem: string;
  splitOn: boolean;
  onSend: (answer: NameCheckAnswer) => void;
}) {
  const id = `name-check-${name.id}`;
  // No kind is chosen for the DM: a tap on Yes with a kind nobody picked would be a wrong answer.
  const [kind, setKind] = useState("");
  const [known, setKnown] = useState("");
  const [spelling, setSpelling] = useState("");
  const [note, setNote] = useState("");
  const room = NOTE_MAX_LETTERS - note.length;
  // Change the words (#2593): starts as the name itself; a longer name picked from the card fills the box.
  const [wholeName, setWholeName] = useState(name.name);
  const [heard, setHeard] = useState(""); // how it was written, when the DM picked a longer name (kept if they then fix it)
  const [changing, setChanging] = useState(false);
  // Split and group (#3406): "They're a group" asks what the group is called.
  const [asking, setAsking] = useState(false);
  const [groupName, setGroupName] = useState("");
  const longer = (name.longerNames ?? []).slice(0, 3);
  // The card keeps its id when its words change (here or in Discord), so the box follows the stored name.
  const shownName = useRef(name.name);
  useEffect(() => {
    if (shownName.current === name.name) return; // (not on the first draw: that would undo a word typed at once)
    shownName.current = name.name;
    setWholeName(name.name);
    setHeard("");
  }, [name.name]);
  return (
    <li>
      <div class="panel stack" role="group" aria-labelledby={`${id}-name`} id={itemDomId(name.id)} tabIndex={-1}>
        <strong id={`${id}-name`}>{name.name}</strong>
        {name.note && (
          <p class="small muted">
            {noteSaysWhere(name.note) ? "" : `${words.noteFrom} `}
            {name.note}
          </p>
        )}
        <SceneLine scene={name.scene} />
        <PlayersKnow line={name.playersText} campaignRef={campaignRef} name={name.name} />
        {NAME_PLACES_LIVE && <NamePlaces places={name.places ?? []} more={name.morePlaces ?? 0} campaignRef={campaignRef} />}
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
        {NAME_CHANGE_WORDS_LIVE && (
          <>
            {longer.length > 0 && (
              <div class="row" role="group" aria-label={words.longerLabel}>
                <span class="small muted">{words.longerLabel}</span>
                {longer.map((option) => (
                  <button key={option} type="button" class="button secondary" disabled={busy} aria-label={words.useName(option)} onClick={() => { setWholeName(option); setHeard(option); setChanging(true); }}>
                    {option}
                  </button>
                ))}
              </div>
            )}
            <details open={changing} onToggle={(e) => setChanging((e.target as HTMLDetailsElement).open)}>
              <summary>{words.changeUse}</summary>
              <fieldset class="stack" disabled={busy}>
                <legend>{words.changeLabel}</legend>
                <p class="small muted">{words.changeHint}</p>
                <div class="row">
                  <label for={`${id}-words`} class="visually-hidden">
                    {words.changeLabel}
                  </label>
                  <input id={`${id}-words`} type="text" maxLength={NAME_MAX_LETTERS} autocomplete="off" autocapitalize="words" spellcheck={false} value={wholeName} onInput={(e) => { const typed = (e.target as HTMLInputElement).value; setWholeName(typed); if (typed.trim() === "") setHeard(""); }} />
                  <button
                    type="button"
                    class="button secondary"
                    disabled={wholeName.trim() === "" || wholeName.trim() === name.name}
                    aria-label={words.changeFor(name.name)}
                    onClick={() => onSend({ action: "change_words", words: wholeName.trim(), ...(heard !== "" ? { heard } : {}) })}
                  >
                    {words.changeUse}
                  </button>
                </div>
              </fieldset>
            </details>
          </>
        )}
        {splitOn && name.isList && (
          <div class="stack">
            <div class="row">
              <button type="button" class="button secondary" disabled={busy} aria-label={`${gw.split}: ${name.name}`} onClick={() => onSend({ action: "split" })}>
                <span aria-hidden="true">{gw.splitIcon}</span> {gw.split}
              </button>
              <button type="button" class="button secondary" disabled={busy} aria-expanded={asking} aria-label={`${gw.isGroup}: ${name.name}`} onClick={() => setAsking(!asking)}>
                <span aria-hidden="true">{gw.groupIcon}</span> {gw.isGroup}
              </button>
            </div>
            <p class="small muted">{gw.explain}</p>
            {(name.groups ?? []).slice(0, 3).map((g) => (
              <div class="stack" key={g.id}>
                <span class="small muted">{gw.hint(g.name, g.members)}</span>
                <button type="button" class="button secondary" disabled={busy} aria-label={`${gw.yesGroup}: ${g.name}`} onClick={() => onSend({ action: "join_group", group: g.id })}>
                  <span aria-hidden="true">{gw.groupIcon}</span> {gw.yesGroup}
                </button>
              </div>
            ))}
            {asking && (
              <div class="stack">
                <label for={`${id}-group`}>{gw.groupAsk}</label>
                <input id={`${id}-group`} type="text" maxLength={gw.groupNameMax} autocomplete="off" autocapitalize="words" spellcheck={false} value={groupName} disabled={busy} onInput={(e) => setGroupName((e.target as HTMLInputElement).value)} />
                <div class="row">
                  <button type="button" class="button" disabled={busy || groupName.trim() === ""} aria-label={`${gw.groupMake}: ${name.name}`} onClick={() => onSend({ action: "make_group", name: groupName.trim() })}>
                    {gw.groupMake}
                  </button>
                  <button type="button" class="button secondary" disabled={busy} onClick={() => { setAsking(false); setGroupName(""); }}>
                    {gw.groupCancel}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
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

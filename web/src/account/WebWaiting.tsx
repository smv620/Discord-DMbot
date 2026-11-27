/** @jsxImportSource preact */
// What waits for the DM on the Story Graph (#2635, #2652): facts PlotBot heard at the table, connections
// found in a backstory (grouped by upload, with Keep all), "Which Mara?" questions and "Who is she?" pronoun
// questions (#2831). The DM decides
// everything: each answer is sent to DMbot, which makes the very calls the DM screen's buttons make, so
// consent, secrets and Undo hold the same. Only a DM of the campaign gets the list from the API; a player
// never sees this part. One answer at a time per item: its buttons wait while it is sent, and the list
// is read again after, so what shows is always the API's own.
import { FIRST_FEELING_LIVE } from "../content/first-feeling-switch";
import { feelingParts } from "./starting-feeling";
import { useEffect, useRef, useState } from "preact/hooks";

import { handBracket, handKeeps } from "../content/hand-over-words";
import { differs, waitingWords as w } from "../content/web-waiting";
import { SOME_KNOW_LIVE } from "../content/some-know-switch";
import { KNOW_LINE_LIVE } from "../content/know-line-switch";
import { ApiError, type AccountApi, type PartyCharacter, type Waiting, type WaitingAnswer, type WaitingBackstory, type WaitingLook } from "./api";
import { currentItem, itemDomId, openItem, pairFromItem } from "./itemLink";
import WaitingBulk from "./WaitingBulk";
import { itemWords } from "../content/item-words";
import WorkProgress from "./WorkProgress";

/** A session's day for a person ("17 October"), from Unix seconds, the same in every time zone. */
const sessionDay = (seconds: number): string =>
  new Date(seconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

/** The wait panel's words and times for an answer on the way (#2921): from the bot's looks once it has sent any, else the
 * page's own clock, so an older API or the first moments before the first look show what they always did. */
function waitShown(sending: { since: number; stage: string; keepAll: boolean; look: WaitingLook | null }) {
  const waits = sending.look?.waits ?? null;
  const progress = sending.look?.progress ?? null;
  // A Keep all says so from its first look, before any count, so it never reads as a plain save.
  const keeping = progress !== null || (sending.keepAll && waits !== null);
  return {
    stage: waits?.stage === "queued" ? w.queuedStage : keeping ? w.keepingConnections : waits ? w.savingStage : sending.stage,
    startedAt: waits ? (waits.now ?? sending.since) - waits.waitedSeconds : sending.since,
    // No time is shown for a look with no server clock or a zero time: a wrong age is worse than none.
    updatedAt: waits && waits.now !== undefined && waits.lastMovedAt > 0 ? waits.lastMovedAt : undefined,
    serverNow: waits?.now ?? sending.since,
    state: waits?.stalled ? ("stalled" as const) : ("running" as const),
    done: progress?.done,
    total: progress?.total,
    unit: progress ? "connections" : undefined,
  };
}

/** Capitalises a line that opens a sentence, so "she grew up with Rook" reads "She grew up with Rook". */
const startCap = (line: string): string => line.charAt(0).toUpperCase() + line.slice(1);

/** The bracket for an item that changed hands, "Claude sold it", or "" for a plain connection or a previous holder the page cannot name. */
const bracketOf = (b: Waiting["backstory"][number]): string => (b.handOver && b.handOverName ? handBracket(b.handOver, b.handOverName) : "");
/** The "Proposed:" line (#4429): on an update only the words that differ from "Now" are bold and the names are plain; on a new card the names are
 * bold and the connection plain, as nothing is compared. */
/** What a "travels with" connection or claim means (#5165): core's own words, first letter capitalised, ending in a full stop. */
function MeansLine({ means }: { means?: string | undefined }) {
  const text = means?.trim();
  if (!text) return null;
  return <p class="small muted">{`${text.charAt(0).toUpperCase()}${text.slice(1)}${text.endsWith(".") ? "" : "."}`}</p>;
}
function proposedOf(b: WaitingBackstory, hand: string) {
  if (!b.now) {
    return (
      <>
        {w.proposedLabel}
        <strong>{b.subject}</strong> {b.how} <strong>{b.object}</strong>
        {hand}
      </>
    );
  }
  const d = differs(b.now.how, b.how);
  return (
    <>
      {w.proposedLabel}
      {b.subject} {d.before && `${d.before} `}
      {d.bold && <strong>{d.bold}</strong>}
      {d.bold && d.after && " "}
      {d.after && (d.bold ? d.after : `${d.after} `)}
      {d.bold && " "}
      {b.object}
      {hand}
    </>
  );
}

const handOf = bracketOf; // (no switch: the fields only arrive with the door that sends them, 0436)
const KEEP_ALL_MAX = 50; // the API keeps at most this many named in one Keep all

type Said = { ok: boolean; text: string; n: number; more?: string } | null;
/** What an answer said back: how many it kept, and whether the bot showed or kept hidden the connection (#3974, #3986). */
type Answered = { kept?: number; shown?: number; held?: number; scene?: number; split?: number; batch?: number };

const errorWords = (error: unknown): string => {
  if (error instanceof ApiError) return w.errors[error.kind] ?? w.errors.other!;
  return w.errors.other!;
};

export default function WebWaiting({
  api,
  campaignRef,
  onChanged,
  onLeft,
  onKinds,
}: {
  api: AccountApi;
  campaignRef: string;
  /** Called after an answer was applied, so the page can read the graph again. */
  onChanged?: () => void;
  /** The claims, backstory and "which" questions still waiting, after each read (#4740, the walk on the Questions page). */
  onLeft?: (left: number) => void;
  /** How many of each Questions kind the list holds now (#5356), so that page can take answered ones off its snapshot. */
  onKinds?: (kinds: { claims: number; family: number; which: number; connections: number }) => void;
}) {
  const [list, setList] = useState<Waiting | null>(null);
  const [failed, setFailed] = useState(false);
  // The DM's change to "can players see it?" on a connection card, by item id; nothing is saved until Add it or Update it (#4430).
  const [flip, setFlip] = useState<Record<string, boolean>>({});
  const [sending, setSending] = useState<{ since: number; stage: string; keepAll: boolean; look: WaitingLook | null } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // A private message's "Open on the website" names one waiting item (#3663): it is opened once the list is here, or the page
  // says it has been answered (here or in Discord).
  const wanted = useRef(((first) => (first !== null && pairFromItem(first) === null ? first : null))(currentItem())); // (a pair of names is a line: the graph opens that)
  const [answered, setAnswered] = useState(false);
  const shown = useRef(campaignRef);
  const [said, setSaid] = useState<Said>(null);
  const [keptAll, setKeptAll] = useState(false); // a Keep all was pressed on this visit: the guesses it left get a count (#3928)
  const [bulkOn, setBulkOn] = useState(false); // an "All true" / "All secret" press is on: every own button waits
  // The claim whose "Which one?" pick can still be taken back (#3463): only the latest pick offers Undo.
  const [undo, setUndo] = useState<string | null>(null);
  // The "Some know" picker open on one claim (#4680 part 1): nobody is ticked when it opens.
  const [picker, setPicker] = useState<{ kind: "claim" | "backstory"; id: string; party: PartyCharacter[] | null; chosen: string[] } | null>(null);
  // The line the ticked characters will give (#5332), asked as the ticks change; a stale answer is dropped by its number.
  // The line is kept with the picker and ticks it was asked for, and shown only while they are still the current ones, so an old
  // line never shows under another picker or an older tick.
  const [knowLine, setKnowLine] = useState<{ key: string; line: string } | null>(null);
  const knowLooks = useRef(0);
  const chosenKey = picker?.chosen.join(",") ?? "";
  const pickKey = picker ? `${campaignRef}:${picker.kind}:${picker.id}:${chosenKey}` : "";
  useEffect(() => {
    const mine = ++knowLooks.current;
    if (!KNOW_LINE_LIVE || !api.knowLine || !picker || picker.chosen.length === 0) return void setKnowLine(null);
    const key = pickKey;
    api
      .knowLine(campaignRef, { characters: picker.chosen, common: false })
      .then((got) => {
        if (mine === knowLooks.current) setKnowLine({ key, line: got.line });
      })
      .catch(() => {
        // Only a preview: with no line, the picker works as before.
        if (mine === knowLooks.current) setKnowLine(null);
      });
    return () => {
      knowLooks.current += 1; // an answer that arrives after the ticks changed or the page was left is dropped
    };
  }, [pickKey]);
  // The last "Some know" can be taken back by its batch, until the next answer.
  const [someUndo, setSomeUndo] = useState<{ id: string; batch: number } | null>(null);
  const counter = useRef(0);
  const latest = useRef(0);
  // A second tap before the page redraws must not send a second answer.
  const inFlight = useRef(false);

  async function read(): Promise<void> {
    if (!api.webWaiting) return;
    const mine = ++latest.current;
    const asked = campaignRef;
    try {
      const got = await api.webWaiting(asked);
      // (another campaign's list is never drawn here: the page may have moved on while this was asked)
      if (mine === latest.current && shown.current === asked) {
        setList(got);
        setFailed(false);
        onLeft?.(got.claims.length + got.backstory.length + got.which.length);
        // A claim that carries choices is one kept for a "Which one?" answer (Questions' "Family ties"), the rest are "Facts to check".
        const family = got.claims.filter((c) => c.choices?.length).length;
        onKinds?.({ claims: got.claims.length - family, family, which: got.which.length, connections: got.backstory.length });
      }
    } catch (error) {
      // A player (or another DM) gets "not found": the part is simply not there.
      if (mine !== latest.current) return;
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setList(null);
      else setFailed(true);
    }
  }

  useEffect(() => {
    if (wanted.current === null || list === null) return;
    const item = wanted.current;
    wanted.current = null; // (once: later reads never pull focus back)
    setTimeout(() => {
      if (!openItem(item)) setAnswered(true);
    }, 0);
  }, [list]);

  useEffect(() => {
    shown.current = campaignRef;
    setList(null);
    setSaid(null);
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function send(
    answer: WaitingAnswer,
    ok: string | ((got: Answered) => string),
    stage: string = w.savingStage,
    own?: Partial<Record<string, string>>,
  ): Promise<void> {
    if (!api.answerWaiting || inFlight.current) return;
    inFlight.current = true;
    setUndo(null);
    setSending({ since: Math.floor(Date.now() / 1000), stage, keepAll: answer.action === "keep_all", look: null });
    setSaid(null);
    try {
      const got = await api.answerWaiting(campaignRef, answer, (look) => setSending((now) => (now ? { ...now, look } : now)));
      // A yes on one connection says whether it was shown to the players or kept hidden (the second line).
      const more = answer.kind === "claim" && answer.action === "confirm"
        ? got.shown ? w.shownOne : got.held ? w.heldOne : got.scene ? w.sceneOne : got.split ? w.splitOne : undefined
        : undefined;
      setSaid({ ok: true, text: typeof ok === "function" ? ok(got) : ok, n: ++counter.current, ...(more ? { more } : {}) });
      setSomeUndo(answer.kind === "claim" && answer.action === "some" && got.batch !== undefined ? { id: answer.id, batch: got.batch } : null);
      onChanged?.();
    } catch (error) {
      // A companion keep refused says why, in the store's own words (#5348); a companion error with no words is the general line.
      const why = error instanceof ApiError && error.kind === "companion" && error.note ? w.companionRefused(error.note) : undefined;
      setSaid({ ok: false, text: why ?? ((error instanceof ApiError && own?.[error.kind]) || errorWords(error)), n: ++counter.current });
      setSomeUndo(null); // a press that failed takes back nothing
    } finally {
      // The buttons wait until the list has been read again, so nobody answers an item that is already gone.
      await read(); // (what shows is always the API's own, whatever happened)
      inFlight.current = false;
      setSending(null);
      headingRef.current?.focus(); // (the answered item has gone, and focus with it)
    }
  }

  // "Some know" (#4680 part 1): the picker lists the party's characters with nobody ticked; Save sends the ticked ids once.
  async function openSome(kind: "claim" | "backstory", id: string): Promise<void> {
    if (sending !== null || bulkOn || !api.partyCharacters) return;
    setPicker({ kind, id, party: null, chosen: [] });
    try {
      const party = await api.partyCharacters(campaignRef);
      setPicker((now) => (now && now.kind === kind && now.id === id ? { ...now, party } : now));
    } catch (error) {
      setPicker(null);
      setSaid({ ok: false, text: errorWords(error), n: ++counter.current });
    }
  }
  function toggleSome(characterId: string): void {
    setPicker((now) =>
      now ? { ...now, chosen: now.chosen.includes(characterId) ? now.chosen.filter((x) => x !== characterId) : [...now.chosen, characterId] } : now,
    );
  }
  async function saveSome(kind: "claim" | "backstory", id: string): Promise<void> {
    if (!picker || picker.kind !== kind || picker.id !== id || picker.party === null || picker.chosen.length === 0) return;
    const names = picker.party.filter((p) => picker.chosen.includes(p.id)).map((p) => p.name);
    const characters = [...picker.chosen];
    setPicker(null);
    if (kind === "backstory") {
      await send(
        { kind: "backstory", action: "some", id, characters },
        () => (names.length === 1 ? w.someDoneBackstoryOne(names[0]!) : w.someDoneBackstoryMany(names.join(", "))),
        w.savingStage,
        { gone: w.someGone, "not-found": w.someNotFound, "bad-target": w.someBackstoryRefused },
      );
      return;
    }
    await send(
      { kind: "claim", action: "some", id, characters },
      () => (names.length === 1 ? w.someDoneOne(names[0]!) : w.someDoneMany(names.join(", "))),
      w.savingStage,
      { gone: w.someGone, "not-found": w.someNotFound },
    );
  }
  // The picker's box, the same for a claim card and a backstory card (#4728): nobody is ticked when it opens; a backstory card
  // also says that its players won't see it until it is said.
  function pickerBox(kind: "claim" | "backstory", id: string, backstoryNote = false) {
    if (!picker) return null;
    return (
      <fieldset class="stack">
        <legend>{w.someLegend}</legend>
        {backstoryNote && <p class="small">{w.someBackstoryNote}</p>}
        <p class="small">{w.someHelp}</p>
        {picker.party === null ? (
          <p class="muted" role="status">{w.someLoading}</p>
        ) : picker.party.length === 0 ? (
          <p class="muted">{w.someEmpty}</p>
        ) : (
          picker.party.map((p) => (
            <label class="choice" key={p.id}>
              <input type="checkbox" checked={picker.chosen.includes(p.id)} onChange={() => toggleSome(p.id)} />
              <span>{p.name}</span>
            </label>
          ))
        )}
        {knowLine && knowLine.key === pickKey && (
          <p class="small" role="status">
            {knowLine.line}
          </p>
        )}
        <div class="row">
          <button type="button" class="button" disabled={busy || picker.party === null || picker.chosen.length === 0} onClick={() => void saveSome(kind, id)}>
            {w.someSave}
          </button>
          <button type="button" class="button secondary" onClick={() => setPicker(null)}>
            {w.someCancel}
          </button>
        </div>
      </fieldset>
    );
  }

  if (!api.webWaiting) return null;
  if (failed && !list)
    return (
      <section class="stack" aria-labelledby="waiting-heading">
        <h3 id="waiting-heading">{w.heading}</h3>
        <p class="warn" role="alert">
          {w.loadFailed}
        </p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.reload}
        </button>
      </section>
    );
  if (!list) return null;

  const busy = sending !== null || bulkOn;
  const { counts } = list;
  const cut = list.claims.length < counts.claims || list.backstory.length < counts.backstory;
  // The DMbot's-check lines below are DM-only by the callers: Web.tsx draws this part for a DM alone, and the API
  // answers only a DM of the campaign. Keep that true before drawing a check anywhere else.
  // A pronoun question can have one name or none, and its "Someone else" keeps the connection with a person
  // who isn't named yet, so it gets its own group and words; a name question keeps the "Neither" words.
  const names = list.which.filter((q) => !q.pronoun);
  const pronouns = list.which.filter((q) => q.pronoun);

  // The backstory connections by upload, in the order the API sent them.
  const uploads: { label: string; items: Waiting["backstory"] }[] = [];
  for (const b of list.backstory) {
    const at = uploads.find((u) => u.label === b.upload);
    if (at) at.items.push(b);
    else uploads.push({ label: b.upload, items: [b] });
  }

  return (
    <section class="stack web-waiting" aria-labelledby="waiting-heading">
      <h3 id="waiting-heading" tabIndex={-1} ref={headingRef}>
        {w.heading}
      </h3>
      <p>
        <strong>{counts.all === 0 ? w.none : w.count(counts.all)}</strong>
      </p>
      {counts.all > 0 && <p class="small">{w.intro}</p>}
      {cut && <p class="small">{w.more}</p>}
      {answered && (
        <p class="note" role="status">
          {itemWords.answered}
        </p>
      )}

      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      {said?.ok && said.more ? (
        <p class="ok" role="status">
          <span key={`${said.n}-more`}>{said.more}</span>
        </p>
      ) : null}
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      {someUndo && said?.ok && (
        <p>
          <button
            type="button"
            class="button secondary"
            disabled={busy}
            onClick={() => {
              const u = someUndo;
              setSomeUndo(null);
              void send({ kind: "claim", action: "undo", id: u.id, batch: u.batch }, w.someUndone);
            }}
          >
            {w.someUndo}
          </button>
        </p>
      )}
      {undo && said?.ok && (
        <p>
          <button
            type="button"
            class="button secondary"
            disabled={busy}
            onClick={() => {
              const id = undo;
              setUndo(null);
              void send({ kind: "claim", action: "unpick", id }, w.tiesUndone);
            }}
          >
            {w.tiesUndo}
          </button>
        </p>
      )}
      {sending && (() => {
        const wait = waitShown(sending);
        return (
          <WorkProgress
            stage={wait.stage}
            startedAt={wait.startedAt}
            updatedAt={wait.updatedAt}
            serverNow={wait.serverNow}
            state={wait.state}
            done={wait.done}
            total={wait.total}
            unit={wait.unit}
          />
        );
      })()}

      {names.length > 0 && (
        <div class="stack">
          <h4>{w.whichHeading}</h4>
          <p class="small">{w.whichNote}</p>
          {names.map((q) => (
            <div key={q.id} class="panel" id={itemDomId(q.id)} tabIndex={-1}>
              <p>
                <strong>{w.whichQuestion(q.word)}</strong>
              </p>
              <p id={`wq-${q.id}`}>{w.whichLine(q.other, q.how, q.word, q.otherIsSubject)}</p>
              {q.check && <p class="small muted">{w.checkLine(q.check)}</p>}
              {q.quote && <p class="muted small">“{q.quote}”</p>}
              {q.secret && <p class="small">{w.secretNote}</p>}
              {q.candidates.some((c) => c.limited) && <p class="small">{w.hiddenNote}</p>}
              <div class="row">
                {q.candidates.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    class="button"
                    disabled={busy}
                    aria-describedby={`wq-${q.id}`}
                    onClick={() => void send({ kind: "which", action: "pick", id: q.id, entity: c.id }, w.done.pick)}
                  >
                    {c.limited ? (
                      <>
                        <span aria-hidden="true">{w.hiddenMark} </span>
                        <span class="visually-hidden">{w.hiddenWord} </span>
                        {c.name}
                      </>
                    ) : (
                      c.name
                    )}
                  </button>
                ))}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-describedby={`wq-${q.id}`}
                  onClick={() => void send({ kind: "which", action: "neither", id: q.id }, w.done.neither)}
                >
                  {w.neither}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {pronouns.length > 0 && (
        <div class="stack">
          <h4>{w.pronounHeading}</h4>
          <p class="small">{w.pronounNote}</p>
          {pronouns.map((q) => (
            <div key={q.id} class="panel" id={itemDomId(q.id)} tabIndex={-1}>
              <p>
                <strong>{w.pronounQuestion(q.word)}</strong>
              </p>
              <p id={`wq-${q.id}`}>{startCap(w.whichLine(q.other, q.how, q.word, q.otherIsSubject))}</p>
              {q.check && <p class="small muted">{w.checkLine(q.check)}</p>}
              {q.quote && <p class="muted small">“{q.quote}”</p>}
              {q.secret && <p class="small">{w.secretNote}</p>}
              {q.candidates.some((c) => c.limited) && <p class="small">{w.hiddenNote}</p>}
              {q.candidates.length === 0 && (
                <p class="small" id={`wq-none-${q.id}`}>
                  {w.pronounNoName}
                </p>
              )}
              <div class="row">
                {q.candidates.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    class="button"
                    disabled={busy}
                    aria-describedby={`wq-${q.id}`}
                    onClick={() => void send({ kind: "which", action: "pick", id: q.id, entity: c.id }, w.done.pick)}
                  >
                    {c.limited ? (
                      <>
                        <span aria-hidden="true">{w.hiddenMark} </span>
                        <span class="visually-hidden">{w.hiddenWord} </span>
                        {c.name}
                      </>
                    ) : (
                      c.name
                    )}
                  </button>
                ))}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-label={w.someoneElseFor(q.word)}
                  aria-describedby={q.candidates.length === 0 ? `wq-${q.id} wq-none-${q.id}` : `wq-${q.id}`}
                  onClick={() => void send({ kind: "which", action: "neither", id: q.id }, w.done.someoneElse)}
                >
                  {w.someoneElse}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {list.claims.some((c) => c.choices?.length) && (
        <div class="stack">
          <h4>{w.tiesHeading}</h4>
          <p class="small">{w.tiesNote}</p>
          {list.claims
            .filter((c) => c.choices?.length)
            .map((c) => (
              <div key={c.id} class="panel" id={itemDomId(c.id)} tabIndex={-1}>
                <p id={`wc-${c.id}`}>{c.statement}</p>
                <p class="small muted">{w.tiesWhy}</p>
                {typeof c.at === "number" && c.at > 0 && <p class="muted small">{w.saidAt(sessionDay(c.at))}</p>}
                <p class="small">{w.tiesPickHelp}</p>
                {c.choices?.map((q) => (
                  <div key={q.side} class="stack">
                    <p>
                      <strong>{w.tiesQuestion(q.words)}</strong>
                    </p>
                    <div class="row">
                      {q.entries.map((e) => (
                        <button
                          key={e.id}
                          type="button"
                          class="button"
                          disabled={busy}
                          aria-describedby={`wc-${c.id}`}
                          onClick={() =>
                            void send({ kind: "claim", action: "pick", id: c.id, side: q.side, entity: e.id }, () => {
                              setUndo(c.id);
                              return w.done.tiesPick;
                            })
                          }
                        >
                          {w.tiesPick(e.name, e.tag)}: {e.note || w.tiesNoNote}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
                <div class="row">
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    aria-describedby={`wc-${c.id}`}
                    onClick={() => void send({ kind: "claim", action: "reject", id: c.id }, w.done.tiesNeither)}
                  >
                    {w.tiesNeither}
                  </button>
                </div>
                <p class="small muted">{w.tiesNeitherHelp}</p>
              </div>
            ))}
        </div>
      )}

      {list.claims.some((c) => !c.choices?.length) && (
        <div class="stack">
          <h4>{w.claimsHeading}</h4>
          <p class="small">{w.claimsNote}</p>
        </div>
      )}
      {list.claims.some((c) => !c.choices?.length) && (
        <div class="stack">
          {list.claims.filter((c) => !c.choices?.length).map((c) => (
            <div key={c.id} class="panel" id={itemDomId(c.id)} tabIndex={-1}>
              <p id={`wc-${c.id}`}>{c.statement}</p>
              <MeansLine means={c.means} />
              {FIRST_FEELING_LIVE && c.startingFeeling && (
                <p class="small">
                  {feelingParts(c.startingFeeling).map((part, i) => (part.bold ? <strong key={i}>{part.text}</strong> : part.text))}
                </p>
              )}
              {typeof c.at === "number" && c.at > 0 && <p class="muted small">{w.saidAt(sessionDay(c.at))}</p>}
              <div class="row">
                <button
                  type="button"
                  class="button"
                  disabled={busy}
                  aria-describedby={`wc-${c.id}`}
                  onClick={() => void send({ kind: "claim", action: "confirm", id: c.id }, w.done.confirm)}
                >
                  {w.confirm}
                </button>
                {c.canSecret && (
                  <button
                    type="button"
                    class="button secret"
                    disabled={busy}
                    aria-describedby={`wc-${c.id}`}
                    onClick={() =>
                      void send({ kind: "claim", action: "confirm", id: c.id, secret: true }, w.done.confirmSecret)
                    }
                  >
                    {w.confirmSecret}
                  </button>
                )}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-describedby={`wc-${c.id}`}
                  onClick={() => void send({ kind: "claim", action: "reject", id: c.id }, w.done.reject)}
                >
                  {w.reject}
                </button>
                {SOME_KNOW_LIVE && c.canSecret && api.partyCharacters !== undefined && (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    aria-describedby={`wc-${c.id}`}
                    onClick={() => void openSome("claim", c.id)}
                  >
                    {w.someButton}
                  </button>
                )}
              </div>
              {picker?.kind === "claim" && picker.id === c.id && pickerBox("claim", c.id)}
            </div>
          ))}
        </div>
      )}
      {/* Outside the claims list, so a press's summary and its "Undo all" stay after the answered claims have left it. */}
      <WaitingBulk
        api={api}
        campaignRef={campaignRef}
        claims={list.claims.filter((c) => !c.choices?.length)}
        locked={sending !== null}
        onRunning={setBulkOn}
        onChanged={async () => {
          await read();
          onChanged?.();
        }}
      />

      {list.backstory.length > 0 && (
        <div class="stack">
          <h4>{w.backstoryHeading}</h4>
          <p class="small">{w.backstoryNote}</p>
          {uploads.map((u) => {
            // (Keep all leaves out a hand-over and DMbot's own guess: only the DM's own press keeps those, as on Discord)
            const ready = u.items.filter((b) => b.ready && !handOf(b) && !b.guess);
            const ids = ready.slice(0, KEEP_ALL_MAX).map((b) => b.id);
            const left = ready.length - ids.length;
            // The same button again after the last card (#5356), so a long list needs no scrolling back up: the same rules, never DMbot's guesses.
            const keepAllRow = (where: string) =>
              ready.length > 1 && (
                <div class="row" data-keep-all={where}>
                  <button
                    type="button"
                    class="button"
                    disabled={busy}
                    onClick={() => {
                      setKeptAll(true);
                      void send({ kind: "backstory", action: "keep_all", ids },
                        (got) => (left > 0 && got.kept !== undefined ? w.keepAllCut(got.kept, left) : w.kept(got.kept)),
                        w.keepingStage(ids.length),
                      );
                    }}
                  >
                    {w.keepAll(ids.length)}
                  </button>
                  <span class="small muted">{w.keepAllPlayers}</span>
                </div>
              );
            return (
              <div key={u.label} class="stack">
                <h5>{u.label ? w.fromUpload(u.label) : w.fromEarlier}</h5>
                {keepAllRow("top")}
                {u.items.map((b) => {
                  // Q2 (#4430): who sees it. A secret name is always hidden; an update starts as it is now; a new one starts shown.
                  const hidden = b.secret ? true : (flip[b.id] ?? b.now?.hidden ?? false);
                  const answer = (action: "keep" | "skip") =>
                    action === "skip"
                      ? ({ kind: "backstory", action: "skip", id: b.id } as const)
                      : ({ kind: "backstory", action: "keep", id: b.id, ...(b.secret ? {} : hidden ? { secret: true } : b.now?.hidden ? { secret: false } : {}) } as const);
                  return (
                  <div key={b.id} class="panel" id={itemDomId(b.id)} tabIndex={-1}>
                    {b.now && <p class="small">{w.nowLine(b.subject, b.now.how, b.object)}</p>}
                    <p id={`wb-${b.id}`}>{proposedOf(b, handOf(b) ? ` (${handOf(b)})` : "")}</p>
                    <MeansLine means={b.means} />
                    {b.check && <p class="small muted">{w.checkLine(b.check)}</p>}
                    {b.guess && <p class="small">{w.guessLine(b.guess)}</p>}
                    {b.quote && <p class="muted small">{w.fromText(b.quote)}</p>}
                    {handOf(b) && b.ready && <p class="small">{handKeeps(b.handOver!, b.handOverName!, b.secret)}</p>}
                    <p class="small">{b.secret ? w.playersSecretName : b.now ? w.playersUpdate(b.now.hidden, hidden) : w.playersNew(hidden)}</p>
                    {b.ready ? (
                      <>
                        <p class="small">{b.now ? w.nothingChangesUpdate : w.nothingChangesAdd}</p>
                        <div class="row">
                          <button type="button" class="button" disabled={busy} aria-describedby={`wb-${b.id}`}
                            onClick={() => void send(answer("keep"), hidden ? w.done.keepSecret : w.done.keep)}>
                            {b.now ? w.updateIt : w.addIt}
                          </button>
                          <button type="button" class="button secondary" disabled={busy} aria-describedby={`wb-${b.id}`}
                            onClick={() => void send(answer("skip"), b.now ? w.done.leftAsIs : w.done.leftOut)}>
                            {b.now ? w.leaveAsIs : w.leaveOut}
                          </button>
                          {SOME_KNOW_LIVE && !b.secret && !handOf(b) && api.partyCharacters !== undefined && (
                            <button type="button" class="button secondary" disabled={busy} aria-describedby={`wb-${b.id}`} onClick={() => void openSome("backstory", b.id)}>
                              {w.someButton}
                            </button>
                          )}
                        </div>
                        {!b.secret && (
                          <div class="row">
                            <button type="button" class="button secondary" disabled={busy} aria-describedby={`wb-${b.id}`}
                              onClick={() => setFlip({ ...flip, [b.id]: !hidden })}>
                              {hidden ? w.letPlayersSee : w.hideFromPlayers}
                            </button>
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        <p class="small">{w.waitingOn(b.waitingOn)}</p>
                        <div class="row">
                          <button type="button" class="button secondary" disabled={busy} aria-describedby={`wb-${b.id}`}
                            onClick={() => void send({ kind: "backstory", action: "skip", id: b.id }, w.done.skip)}>
                            {w.skip}
                          </button>
                        </div>
                      </>
                    )}
                    {picker?.kind === "backstory" && picker.id === b.id && pickerBox("backstory", b.id, true)}
                  </div>
                  );
                })}
                {u.items.length > 1 && keepAllRow("bottom")}
              </div>
            );
          })}
          {keptAll && list.backstory.some((b) => b.guess) && (
            <p class="small">{w.guessesLeft(list.backstory.filter((b) => b.guess).length)}</p>
          )}
        </div>
      )}
    </section>
  );
}

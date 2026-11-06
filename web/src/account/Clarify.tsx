/** @jsxImportSource preact */
// One name waiting to be checked, on its own page (#3604; Discord's "More options…" links here). The scene and where the
// name was said show at once; DMbot's best guesses are ready answers; a search over every name lets the game master
// pick the one it matches, or make a new entry; the last answer is "Trivial: no need to track this name" (the existing
// "not a name" answer: the entry is dropped and not asked about again). Every answer is queued for the bot, the page
// follows it, and the last one can be undone. DMbot only suggests: nothing changes until a button is pressed.
// Shown to the campaign's DM only (the door refuses anyone else); nothing here is kept or cached.
import { useEffect, useRef, useState } from "preact/hooks";

import { clarifyWords as w } from "../content/name-clarify-words";
import { ApiError, type AccountApi, type ClarifyName, type NameAnswerResult, type NameCheckAnswer } from "./api";
import NamePlaces from "./NamePlaces";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLL_LIMIT = 40;
const MATCHES = 8;
type Match = { id: string; name: string; kind: string; scene: string };

export default function Clarify({ api, refId, nameId }: { api: AccountApi; refId: string; nameId: string }) {
  const [name, setName] = useState<ClarifyName | null>(null);
  const [gone, setGone] = useState(false);
  const [failed, setFailed] = useState("");
  const [working, setWorking] = useState(false);
  const [report, setReport] = useState("");
  const [last, setLast] = useState<{ text: string; batch: number | null } | null>(null);
  const [undone, setUndone] = useState(false);
  const [undoneWho, setUndoneWho] = useState("");
  const [find, setFind] = useState("");
  const [kind, setKind] = useState("");
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchFailed, setSearchFailed] = useState(false);
  const [capped, setCapped] = useState(false);
  const [picked, setPicked] = useState<{ id: string; name: string } | null>(null);
  const [newKind, setNewKind] = useState("");
  const [askTrivial, setAskTrivial] = useState(false);
  const flight = useRef(false);
  const lastWho = useRef(""); // the name just answered, for the line after Undo
  const askBox = useRef<HTMLParagraphElement>(null);
  const trivialBox = useRef<HTMLParagraphElement>(null);
  const resultBox = useRef<HTMLDivElement>(null);
  const seen = useObservedWait(working, String(last?.text ?? ""));

  async function load(): Promise<void> {
    if (!api.clarifyName) return;
    try {
      setName(await api.clarifyName(refId, nameId));
      setGone(false);
      setFailed("");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "not-found") setGone(true);
      else setFailed(kind === "not-allowed" ? w.notDm : w.loadFailed);
    }
  }
  useEffect(() => {
    void load();
  }, [refId, nameId]);

  // The search waits for a pause in the typing; a late answer to an old word is dropped.
  useEffect(() => {
    const wanted = find.trim();
    if (wanted === "" || !api.allNames) {
      setMatches(null);
      setSearching(false);
      return;
    }
    let live = true;
    setMatches(null);
    setSearchFailed(false);
    setSearching(true);
    const timer = setTimeout(() => {
      api
        .allNames?.(refId, { q: wanted, kind, sort: "name", page: 1, size: 20 })
        .then((got) => {
          if (!live) return;
          const named = got.names.filter((n) => !n.unnamed && n.id !== nameId);
          setCapped(named.length > MATCHES);
          setMatches(named.slice(0, MATCHES).map((n) => ({ id: n.id, name: n.name, kind: got.kinds.find((k) => k.key === n.kind)?.label ?? "", scene: n.scene ?? "" })));
          setSearching(false);
        })
        .catch(() => {
          if (!live) return;
          setSearchFailed(true);
          setSearching(false);
        });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [find, kind, api, refId, nameId]);

  const problem = (error: unknown): string => {
    const k = error instanceof ApiError ? error.kind : "network";
    return w.problems[k] ?? w.problems.server ?? "";
  };

  // Queue one answer, then follow the bot's word on it until it is applied or refused.
  async function send(answer: NameCheckAnswer, text: string): Promise<void> {
    if (flight.current || !api.answerNameCheck) return;
    flight.current = true;
    lastWho.current = name?.name ?? "";
    setWorking(true);
    setReport("");
    setUndone(false);
    setPicked(null);
    setAskTrivial(false);
    try {
      await api.answerNameCheck(refId, nameId, answer);
      for (let looks = 0; looks < POLL_LIMIT; looks += 1) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        let got: NameAnswerResult;
        try {
          got = await api.nameAnswerResult!(refId, nameId);
        } catch (error) {
          if (error instanceof ApiError && error.kind === "not-found") {
            // No answer on record: it was applied and cleared.
            setLast({ text, batch: null });
            setName(null);
            setGone(true);
            return;
          }
          throw error;
        }
        if (got.state === "done") {
          setLast({ text, batch: got.batch });
          setName(null);
          setGone(true);
          return;
        }
        if (got.state === "failed") {
          setReport(w.problems[got.error?.replaceAll("_", "-") ?? ""] ?? w.problems.gone ?? "");
          return;
        }
      }
      setReport(w.quiet);
    } catch (error) {
      setReport(problem(error));
    } finally {
      flight.current = false;
      setWorking(false);
    }
  }

  async function undo(): Promise<void> {
    if (flight.current || !api.answerNameCheck || last?.batch == null) return;
    flight.current = true;
    setWorking(true);
    setReport("");
    try {
      await api.answerNameCheck(refId, nameId, { action: "undo", batch: last.batch });
      for (let looks = 0; looks < POLL_LIMIT; looks += 1) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        try {
          const got = await api.nameAnswerResult!(refId, nameId);
          if (got.state === "failed") {
            setReport(w.problems[got.error?.replaceAll("_", "-") ?? ""] ?? w.problems.gone ?? "");
            return;
          }
        } catch (error) {
          if (error instanceof ApiError && error.kind === "not-found") break;
          throw error;
        }
      }
      setUndoneWho(lastWho.current);
      setLast(null);
      setUndone(true);
      setGone(false);
      await load();
    } catch (error) {
      setReport(problem(error));
    } finally {
      flight.current = false;
      setWorking(false);
    }
  }

  // After an answer the form is gone: focus goes to the line that says what happened, so keyboard and screen-reader users aren't dropped.
  useEffect(() => {
    if (last) resultBox.current?.focus();
  }, [last]);

  if (!api.clarifyName || !api.answerNameCheck || !api.nameAnswerResult) return <p class="muted">{w.notYet}</p>;
  if (failed) return <p class="warn" role="alert">{failed}</p>;
  const who = name?.name ?? "";
  return (
    <div class="stack clarify">
      {last && (
        <div class="stack" role="status" tabIndex={-1} ref={resultBox}>
          <p>{last.text}</p>
          {last.batch !== null && (
            <p>
              <button type="button" class="button secondary" aria-disabled={working} onClick={() => void undo()}>
                {w.undo}
              </button>
            </p>
          )}
        </div>
      )}
      {undone && <p class="small" role="status">{w.undone(undoneWho)}</p>}
      {working && seen.startedAt !== null && (
        <WorkProgress stage={w.stage.working} startedAt={seen.startedAt} updatedAt={seen.movedAt} state={seen.quiet ? "stalled" : "running"} whatToDo={w.quiet} />
      )}
      {report && <p class="warn small" role="alert">{report}</p>}
      {gone && !last && !undone && <p class="muted">{w.none}</p>}
      {!name && !gone && <p class="muted" role="status">{w.loading}</p>}
      {name && (
        <>
          <h2>{who}</h2>
          {name.note !== "" && <p class="muted">{name.note}</p>}
          <section class="stack" aria-labelledby="clarify-scene">
            <h3 id="clarify-scene">{w.sceneHeading}</h3>
            <p>{name.scene ? name.scene : <span class="muted">{w.noScene}</span>}</p>
            <NamePlaces places={name.places} more={name.morePlaces} campaignRef={refId.split(".")[0] ?? refId} />
          </section>
          <section class="stack" aria-labelledby="clarify-top">
            <h3 id="clarify-top">{w.topHeading}</h3>
            <p class="small muted">{w.topHint}</p>
            {(name.answers ?? []).length === 0 ? (
              <p class="small muted">{w.noTop}</p>
            ) : (
              <div class="row">
                {(name.answers ?? []).map((a) => (
                  <button key={a.label} type="button" class="button" aria-disabled={working} onClick={() => !working && void send(a.answer, a.answer.action === "same" ? w.done.same(who, a.label.replace(/^a\.k\.a\. /, "")) : w.done.confirm(who))}>
                    {a.label}
                  </button>
                ))}
              </div>
            )}
          </section>
          <section class="stack" aria-labelledby="clarify-search">
            <h3 id="clarify-search">{w.searchHeading}</h3>
            <label class="field" for="clarify-find">{w.searchLabel}</label>
            <input id="clarify-find" type="text" value={find} autoComplete="off" aria-describedby="clarify-find-hint" onInput={(e) => setFind((e.target as HTMLInputElement).value)} />
            <p class="small muted" id="clarify-find-hint">{w.searchHint}</p>
            <label class="field" for="clarify-kind">{w.kindLabel}</label>
            <select id="clarify-kind" value={kind} onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
              <option value="">{w.anyKind}</option>
              {name.kinds.map((k) => (
                <option key={k.key} value={k.key}>{k.label}</option>
              ))}
            </select>
            {matches !== null && matches.length > 0 && (
              <ul class="stack plain-list">
                {matches.map((m) => (
                  <li key={m.id}>
                    <button type="button" class="button secondary" aria-label={w.pick(m.name, m.kind)} aria-disabled={working} onClick={() => { if (working) return; setPicked({ id: m.id, name: m.name }); setTimeout(() => askBox.current?.focus(), 0); }}>
                      {m.name}{m.kind ? ` (${m.kind})` : ""}
                    </button>
                    {m.scene && <span class="small muted"> {m.scene}</span>}
                  </li>
                ))}
              </ul>
            )}
            {capped && <p class="small muted">{w.more}</p>}
            <p class="small" role="status">
              {searchFailed ? w.searchFailed : searching ? w.searching : matches !== null ? (matches.length === 0 ? w.noMatch : w.found(matches.length)) : ""}
            </p>
            {picked && (
              <div class="stack" role="group" aria-label={w.askSame(who, picked.name)}>
                <p tabIndex={-1} ref={askBox}>{w.askSame(who, picked.name)}</p>
                <div class="row">
                  <button type="button" class="button" aria-disabled={working} onClick={() => !working && void send({ action: "same", known: picked.id }, w.done.same(who, picked.name))}>{w.yes}</button>
                  <button type="button" class="button secondary" onClick={() => setPicked(null)}>{w.no}</button>
                </div>
              </div>
            )}
          </section>
          <section class="stack" aria-labelledby="clarify-new">
            <h3 id="clarify-new">{w.newHeading}</h3>
            <label class="field" for="clarify-new-kind">{w.newLabel}</label>
            <select id="clarify-new-kind" value={newKind} onChange={(e) => setNewKind((e.target as HTMLSelectElement).value)}>
              <option value="">{w.chooseKind}</option>
              {name.kinds.map((k) => (
                <option key={k.key} value={k.key}>{k.label}</option>
              ))}
            </select>
            <p>
              <button type="button" class="button secondary" aria-disabled={working || newKind === ""} aria-describedby="clarify-need-kind" onClick={() => !working && newKind !== "" && void send({ action: "confirm", kind: newKind }, w.done.confirm(who))}>
                {w.newButton}
              </button>
              {newKind === "" && <span class="small muted" id="clarify-need-kind"> {w.needKind}</span>}
            </p>
          </section>
          <section class="stack" aria-labelledby="clarify-trivial">
            <h3 id="clarify-trivial">{w.trivialHeading}</h3>
            {askTrivial ? (
              <div class="stack" role="group" aria-label={w.trivialAsk}>
                <p tabIndex={-1} ref={trivialBox}>{w.trivialAsk}</p>
                <div class="row">
                  <button type="button" class="button" aria-disabled={working} onClick={() => !working && void send({ action: "not_a_name" }, w.done.not_a_name(who))}>{w.trivialYes}</button>
                  <button type="button" class="button secondary" onClick={() => setAskTrivial(false)}>{w.trivialNo}</button>
                </div>
              </div>
            ) : (
              <p>
                <button type="button" class="button secondary" aria-disabled={working} onClick={() => { if (working) return; setAskTrivial(true); setTimeout(() => trivialBox.current?.focus(), 0); }}>{w.trivial}</button>
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

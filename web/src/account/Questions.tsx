/** @jsxImportSource preact */
// The Questions page (#3438): every question DMbot waits on, for each campaign the person is a game master
// of, with the same words as Discord's "❓ Questions for you" (#3435). It shows each campaign's counts by kind,
// oldest first, and for each kind either the page that answers it or how to answer it in Discord, never a dead
// button. Counts and campaign names only: the cards come from each kind's own page. The counts are the bot's
// (`question_counts`); older than five minutes, the page says it is catching up.
import { DEED_QUESTIONS_LIVE } from "../content/deed-questions-switch";
import { matchWords } from "../content/name-matches-words";
import { NAME_MATCHES_LIVE } from "../content/name-matches-switch";
import { useEffect, useRef, useState } from "preact/hooks";

import { RECAP_NAME_CHECKS_LIVE } from "../content/recap-words";
import { WEB_READINGS_LIVE } from "../content/web-readings";
import { WEB_WAITING_LIVE } from "../content/web-waiting";
import { QUESTIONS_LIVE } from "../content/questions-switch";
import { kindWords, type QuestionHow, questionsPage as words } from "../content/questions-words";
import { type AccountApi, ApiError, type QuestionCampaign, type QuestionCounts, type QuestionKind } from "./api";
import { campaignHref } from "./campaignLinks";
import { TRAIT_QUESTIONS_LIVE } from "../content/trait-questions-switch";
import { SECRET_QUESTIONS_LIVE } from "../content/secret-questions-switch";
import { PARTY_JOIN_LIVE } from "../content/party-join-switch";
import { PARTY_LEAVES_LIVE } from "../content/party-leaves-switch";
import { REVEALS_LIVE } from "../content/reveals-switch";
import { HEARD_LIVE } from "../content/heard-switch";
import { WORLD_NOTES_LIVE } from "../content/world-notes-switch";
import { THREADS_LIVE } from "../content/threads-switch";
import { MAPS_LIVE } from "../content/maps-switch";
import { RULE_UPDATES_LIVE } from "../content/rule-updates-switch";
import { MORE_NAMES_LIVE } from "../content/more-names-switch";
import { LOOKALIKES_LIVE } from "../content/lookalikes-switch";
import MoreNames from "./MoreNames";
import QuestionsHere, { groupOf, type HereGroup } from "./QuestionsHere";

/** The bot counts every minute or so: five minutes without a count and the numbers are said to be old. */
export const CATCHING_UP_SECONDS = 300;

function href(ref: string, to: "recaps" | "graph" | "names" | "maps" | "houserules" | "sharing"): string {
  const e = encodeURIComponent(ref);
  if (to === "sharing") return "/account/sharing"; // lists every campaign the person runs, so it takes no ref (#4875)
  return to === "recaps"
    ? `/account/recaps?campaign=${e}`
    : to === "graph"
      ? `/account/web/${e}`
      : to === "maps"
        ? `/account/maps/${e}`
        : to === "houserules"
          ? `/account/house-rules/${e}`
          : `/account/names/${e}`;
}

/** Oldest question first; a kind with no time keeps its place after those that have one. */
export function oldestFirst(kinds: QuestionKind[]): QuestionKind[] {
  return [...kinds]
    .filter((k) => k.count > 0)
    .sort((a, b) => (a.oldest ?? Number.MAX_SAFE_INTEGER) - (b.oldest ?? Number.MAX_SAFE_INTEGER));
}

/** The order a walk through everything goes in, as Discord's (`dm_questions.ORDER`, #4192): a connection is only asked once both its
 * names are answered, and a fact reads better once its names are settled. Kinds not named here follow, oldest first. */
const ORDER = ["names", "heard", "lookalikes", "claims", "connections", "which", "family"];

/** The page's kinds in the fixed order: names, then what was said, then the connections, then the rest oldest first. */
export function inFixedOrder(kinds: QuestionKind[]): QuestionKind[] {
  const rank = (kind: string): number => (ORDER.includes(kind) ? ORDER.indexOf(kind) : ORDER.length);
  return oldestFirst(kinds).sort((a, b) => rank(a.kind) - rank(b.kind));
}

/** Kinds this page has no words for are one line, "Other questions", with their counts added up. */
export function mergeUnknown(kinds: QuestionKind[]): QuestionKind[] {
  const known = kinds.filter((k) => Object.hasOwn(words.kinds, k.kind));
  const unknown = kinds.filter((k) => !Object.hasOwn(words.kinds, k.kind) && k.count > 0);
  if (unknown.length === 0) return known;
  const oldest = unknown.map((k) => k.oldest).filter((t): t is number => t !== null);
  return [...known, { kind: "other", count: unknown.reduce((n, k) => n + k.count, 0), oldest: oldest.length ? Math.min(...oldest) : null }];
}

/** Where a kind is answered today: a kind that opens its cards here falls back to its link while the page can't read them. */
function howFor(kind: string, api: AccountApi): QuestionHow {
  // The lineage and class question (#3946): answered here once its switch is on, else in Discord, as its words say.
  if (kind === "traits") return TRAIT_QUESTIONS_LIVE && api.traitOffers !== undefined ? "here" : "discord";
  // The 🤫 Known or secret? question (#4598): answered here once its switch is on, else in Discord.
  if (kind === "secrets") return SECRET_QUESTIONS_LIVE && api.secretQuestions !== undefined ? "here" : "discord";
  // The join question (#4778 part b): answered here once its switch is on, else in Discord.
  if (kind === "party_joins") return PARTY_JOIN_LIVE && api.joinQuestions !== undefined ? "here" : "discord";
  // The "Still in the party?" question (#4895): answered here once its switch is on, else in Discord.
  if (kind === "party_leaves") return PARTY_LEAVES_LIVE && api.leaveQuestions !== undefined ? "here" : "discord";
  // The "Deeds to record" card (#3591): answered here once its switch is on, else in Discord.
  if (kind === "deeds") return DEED_QUESTIONS_LIVE && api.deedQuestions !== undefined ? "here" : "discord";
  // What the characters learned (#3863 step 2): answered here once its switch is on, else in Discord.
  if (kind === "reveals") return REVEALS_LIVE && api.revealQuestions !== undefined ? "here" : "discord";
  // Names that look alike (#5259): answered here once its switch is on, else Discord's own panel (never a link to a page that cannot answer).
  if (kind === "lookalikes") return LOOKALIKES_LIVE && api.lookAlikes !== undefined ? "here" : "discord";
  // The "Did they mean…?" question (#4841 B1): answered here once its switch is on, else in Discord.
  if (kind === "heard") return HEARD_LIVE && api.heardQuestions !== undefined ? "here" : "discord";
  // The three kinds answered on another page (#4841 A): a link to it while that page's switch is on, else in Discord.
  if (kind === "world_notes") return WORLD_NOTES_LIVE ? "link" : "discord";
  if (kind === "threads") return THREADS_LIVE ? "link" : "discord";
  if (kind === "maps") return MAPS_LIVE ? "link" : "discord";
  // A newer version of a followed house-rules set (#4845): a link to the House rules page while its switch is on.
  if (kind === "rules") return RULE_UPDATES_LIVE ? "link" : "discord";
  const how = kindWords(kind).how;
  if (how !== "here") return how;
  const group = groupOf(kind);
  const can =
    group === "names"
      ? RECAP_NAME_CHECKS_LIVE && api.campaignNameChecks !== undefined
      : group === "waiting"
        ? WEB_WAITING_LIVE && api.webWaiting !== undefined
        : group === "readings"
          ? WEB_READINGS_LIVE && api.webReadings !== undefined
          : false;
  // Never a link to a page whose panel is off (#5225: the Story Graph's waiting list sent a DM to an empty page): say where it can be answered.
  return can ? "here" : "discord";
}

function Campaign({ campaign, serverNow, api }: { campaign: QuestionCampaign; serverNow: number; api: AccountApi }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  // What was answered here since the bot counted (#5356): the bot's numbers are a snapshot of a minute or so ago and are not read again
  // after an answer, so the page takes off what its own waiting list lost. The first report of a visit is the baseline; only the drop
  // from it is taken off, so the two lists' different counting never shows (only what was answered here does).
  const [answeredHere, setAnswered] = useState<Record<string, number>>({});
  const baseline = useRef<Record<string, number> | null>(null);
  // What the open waiting list holds of each kind now, to say how many of a kind's count are in it and how many are in the lists below.
  const [here, setHere] = useState<Record<string, number> | null>(null);
  const onKinds = (now: Record<string, number>): void => {
    // (a list that grows, as new items arrive, moves its baseline up, so only a real drop is ever taken off)
    setHere({ ...now });
    const base = (baseline.current = Object.fromEntries(Object.keys(now).map((k) => [k, Math.max(baseline.current?.[k] ?? 0, now[k] ?? 0)])));
    setAnswered(Object.fromEntries(Object.keys(now).map((k) => [k, Math.max(0, (base[k] ?? 0) - (now[k] ?? 0))])));
  };
  const counted = campaign.kinds.map((k) => ({ ...k, count: Math.max(0, k.count - (answeredHere[k.kind] ?? 0)) }));
  // The walk and the panels' places come from the bot's own list, which does not change while the DM answers, so a part never slides
  // out from under the walk; what is shown (and its numbers) is the same list with the answered ones taken off.
  const fixed = inFixedOrder(mergeUnknown(campaign.kinds)).filter((k) => kindWords(k.kind).how !== "hidden");
  const adjusted = inFixedOrder(mergeUnknown(counted));
  const shownCount = (kind: string): number => adjusted.find((a) => a.kind === kind)?.count ?? 0;
  // A kind whose last item was answered goes from the list at once (#5356). The group's list is hosted under the first line still shown
  // (`shownLead`), so it moves to the next line of its group rather than vanishing while its other kinds still wait; the walk's parts
  // and their leads stay from the bot's own list (`lead`), so a part never slides out from under the walk.
  const kinds = fixed.map((k) => ({ ...k, count: shownCount(k.kind) })).filter((k) => k.count > 0);
  // The first line of each group carries its toggle and panel; the others say they are answered with it.
  const lead = new Map<string, string>();
  for (const k of fixed) {
    const g = groupOf(k.kind);
    if (g !== null && howFor(k.kind, api) === "here" && !lead.has(g)) lead.set(g, k.kind);
  }
  const shownLead = new Map<string, string>();
  for (const k of kinds) {
    const g = groupOf(k.kind);
    if (g !== null && howFor(k.kind, api) === "here" && !shownLead.has(g)) shownLead.set(g, k.kind);
  }
  // The line an answered kind leaves: its list moves to the next line of its group, or, with the whole group answered, closes and says so.
  const [allAnswered, setAllAnswered] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const gone = [...open].filter((kind) => fixed.some((f) => f.kind === kind) && !kinds.some((k) => k.kind === kind));
    if (gone.length === 0) return;
    setOpen((was) => {
      const next = new Set(was);
      for (const kind of gone) {
        next.delete(kind);
        const heir = shownLead.get(groupOf(kind) ?? "");
        if (heir !== undefined) next.add(heir);
      }
      return next;
    });
    const done = gone.filter((kind) => !shownLead.has(groupOf(kind) ?? ""));
    if (done.length > 0) {
      setAllAnswered(words.kindDone(kindWords(done[0] ?? "").label));
      headingRef.current?.focus(); // (the list that held focus has closed)
    }
  }, [answeredHere]);
  // The walk (#4193): one button through everything that can be answered here, in the fixed order, a part at a time. Its parts are
  // the panels that open here (the first kind of each group stands for the group, with the whole group's count).
  const parts = [...lead.entries()].map(([group, kind]) => ({
    group: group as HereGroup,
    label: kindWords(kind).label,
    count: fixed.filter((k) => groupOf(k.kind) === group && howFor(k.kind, api) === "here").reduce((n, k) => n + shownCount(k.kind), 0),
  }));
  const [walk, setWalk] = useState<number | null>(null);
  const [resume, setResume] = useState(0);
  const [stopped, setStopped] = useState(false); // (stopping on the first part must still read "Carry on")
  const [note, setNote] = useState<string | null>(null);
  // How many items the walking part still has, as its panel reports them (#4740); null until the panel has reported.
  const [left, setLeft] = useState<number | null>(null);
  const movedFrom = useRef<number | null>(null); // the part whose empty list already moved the walk on: once per part
  const statusRef = useRef<HTMLParagraphElement>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const focusStart = useRef(false); // Stop or the end sends focus back to the start button
  const goTo = (i: number): void => {
    // (an earlier "stopped" or "ended" line goes when a walk starts again)
    setNote(walk !== null ? words.partDone(parts[walk]?.label ?? "", parts[i]?.label ?? "", parts[i]?.count ?? 0) : null);
    setLeft(null);
    movedFrom.current = null;
    focusStart.current = false;
    setWalk(i);
    setResume(i);
    setStopped(false);
  };
  const stop = (): void => {
    focusStart.current = true;
    setResume(walk ?? 0);
    setStopped(true);
    setWalk(null);
    setNote(words.walkStopped);
  };
  const finish = (): void => {
    focusStart.current = true;
    setResume(0);
    setStopped(false);
    setWalk(null);
    setNote(words.walkEnded);
  };
  const walking = walk !== null ? parts[walk] : undefined;
  // An empty part moves the walk on by itself (#4740): once per part, and the last part's end finishes the walk.
  useEffect(() => {
    if (walk === null || left !== 0 || movedFrom.current === walk) return;
    movedFrom.current = walk;
    if (walk + 1 < parts.length) goTo(walk + 1);
    else finish();
  }, [left, walk]);
  // Focus follows the walk: the progress line when a part starts or changes, the start button after Stop or the end (#4740).
  useEffect(() => {
    if (walk !== null) statusRef.current?.focus();
    else if (focusStart.current) {
      startRef.current?.focus();
      focusStart.current = false;
    }
  }, [walk]);
  // The bot has never counted this campaign (null) or not lately: the numbers can't be vouched for, so there is no all-clear.
  // DMbot's own matches (#3701): shown with the Names page's panel, so both go live together.
  const guesses = NAME_MATCHES_LIVE && api.nameMatches !== undefined ? (campaign.guesses ?? 0) : 0;
  const old = campaign.countedAt === null || serverNow - campaign.countedAt > CATCHING_UP_SECONDS;
  return (
    <section class="panel stack" aria-labelledby={`q-${campaign.ref}`}>
      <h2 id={`q-${campaign.ref}`} tabIndex={-1} ref={headingRef}>
        <a href={campaignHref(campaign.ref)}>{campaign.name}</a>
      </h2>
      {old && <p class="small muted" role="status">{words.catchingUp}</p>}
      {allAnswered !== null && (
        <p class="small" role="status">
          {allAnswered}
        </p>
      )}
      {guesses > 0 && (
        <p>
          {matchWords.countOf(guesses)}{" "}
          <a href={`/account/names/${encodeURIComponent(campaign.ref)}`}>{matchWords.checkThem}</a>
        </p>
      )}
      {kinds.length === 0 ? (
        campaign.countedAt === null || guesses > 0 ? null : <p>{words.nothing}</p>
      ) : (
        <>
          <p>{words.campaignLine(kinds.reduce((n, k) => n + k.count, 0))}</p>
          {parts.length >= 2 && (
            <div class="stack">
              {walking === undefined ? (
                <button ref={startRef} type="button" class="button" onClick={() => goTo(resume)}>
                  {!stopped
                    ? words.checkEverything(parts.reduce((n, p) => n + p.count, 0))
                    : words.carryOn(parts.slice(resume).reduce((n, p) => n + p.count, 0))}
                </button>
              ) : (
                <section class="stack" aria-label={walking.label}>
                  <p ref={statusRef} tabIndex={-1} role="status">
                    <strong>{words.partLine(walking.label, (walk ?? 0) + 1, parts.length, left ?? walking.count)}</strong>
                  </p>
                  <QuestionsHere
                    key={walking.group}
                    api={api}
                    campaignRef={campaign.ref}
                    group={walking.group}
                    id={`q-${campaign.ref}-walk`}
                    waiting={walking.count}
                    onLeft={setLeft}
                    onKinds={onKinds}
                  />
                  <div class="row">
                    {(walk ?? 0) + 1 < parts.length ? (
                      <button type="button" class="button" onClick={() => goTo((walk ?? 0) + 1)}>
                        {words.nextPart(parts[(walk ?? 0) + 1]?.label ?? "", parts[(walk ?? 0) + 1]?.count ?? 0)}
                      </button>
                    ) : (
                      <button type="button" class="button" onClick={finish}>
                        {words.finishWalk}
                      </button>
                    )}
                    <button type="button" class="button secondary" onClick={stop}>
                      {words.stopForNow}
                    </button>
                  </div>
                </section>
              )}
              {note !== null && (
                <p class="small muted" role="status">
                  {note}
                </p>
              )}
            </div>
          )}
          <ul class="stack">
            {kinds.map((k) => {
              const w = kindWords(k.kind);
              return (
                <li key={k.kind} class="stack">
                  <strong>{here !== null && Object.hasOwn(here, k.kind) && k.count > (here[k.kind] ?? 0) ? words.kindLineSplit(w.label, k.count, here[k.kind] ?? 0) : words.kindLine(w.label, k.count)}</strong>
                  {k.kind === "more_names" && MORE_NAMES_LIVE && api.moreNames !== undefined ? (
                    <MoreNames api={api} campaignRef={campaign.ref} />
                  ) : howFor(k.kind, api) === "here" && shownLead.get(groupOf(k.kind) ?? "") !== k.kind ? (
                    <span class="small muted">{words.answeredWith(kindWords(shownLead.get(groupOf(k.kind) ?? "") ?? "").label)}</span>
                  ) : howFor(k.kind, api) === "here" ? (
                    <>
                      <button
                        type="button"
                        class="button secondary"
                        aria-expanded={open.has(k.kind)}
                        aria-controls={`q-${campaign.ref}-${k.kind}`}
                        aria-label={`${open.has(k.kind) ? words.hideHere : words.showHere}: ${w.label}`}
                        onClick={() =>
                          setOpen((was) => {
                            const next = new Set(was);
                            if (!next.delete(k.kind)) next.add(k.kind);
                            return next;
                          })
                        }
                      >
                        {open.has(k.kind) ? words.hideHere : words.showHere}
                      </button>
                      {open.has(k.kind) && walking?.group !== groupOf(k.kind) && (
                        <QuestionsHere api={api} campaignRef={campaign.ref} group={groupOf(k.kind) ?? "names"} id={`q-${campaign.ref}-${k.kind}`} onKinds={onKinds} waiting={k.count} />
                      )}
                    </>
                  ) : howFor(k.kind, api) === "link" && w.to ? (
                    <>
                      <a class="button secondary" href={href(campaign.ref, w.to)}>
                        {w.link}
                      </a>
                      {w.also && (
                        <a class="button secondary" href={href(campaign.ref, w.also.to)}>
                          {w.also.link}
                        </a>
                      )}
                    </>
                  ) : (
                    <span class="small muted">{w.where ? words.discordAt(w.where) : words.discord}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

export default function Questions({ api }: { api: AccountApi }) {
  const [data, setData] = useState<QuestionCounts | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!QUESTIONS_LIVE || !api.myQuestions) return;
    let live = true;
    api
      .myQuestions()
      .then((got) => live && setData(got))
      .catch((error) => {
        // A signed-out visitor is sent to sign in by the page around this one; anything else is a failed load.
        if (live && !(error instanceof ApiError && error.kind === "signed-out")) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [api]);

  if (!QUESTIONS_LIVE || !api.myQuestions) return null;
  if (failed)
    return (
      <p class="warn" role="alert">
        {words.failed}
      </p>
    );
  if (data === null)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (data.campaigns.length === 0) return <p>{words.none}</p>;
  return (
    <div class="stack">
      <p class="muted">{words.intro}</p>
      {data.campaigns.map((c) => (
        <Campaign key={c.ref} campaign={c} serverNow={data.serverNow} api={api} />
      ))}
      {data.campaigns.some((c) => oldestFirst(c.kinds).some((k) => kindWords(k.kind).how === "discord")) && <p class="small muted">{words.discordWhere}</p>}
    </div>
  );
}

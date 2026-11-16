/** @jsxImportSource preact */
// The quest sheet in the Story Graph's detail pane (#4547, the web half of #4540): a kept story thread's kind and state, what it is about,
// who is in it, its reward and help, its steps, and the questions waiting on it. DM only, behind QUEST_SHEET_LIVE. Words and counts only:
// no id is shown. A refused or missing thread shows nothing, and the pane is as it was.
import { useEffect, useRef, useState } from "preact/hooks";

import { questSheetWords as w } from "../content/quest-sheet-words";
import type { AccountApi, ThreadCard, ThreadCardPart } from "./api";

type Load = { kind: "loading" } | { kind: "card"; card: ThreadCard } | { kind: "none" };

// A name in the sheet is a button that selects that node in the map.
function Name({ id, name, onSelect }: { id: string; name: string; onSelect: (id: string) => void }) {
  return (
    <button type="button" class="link-button" onClick={() => onSelect(id)}>
      {name}
    </button>
  );
}

export default function QuestSheet({
  api,
  refId,
  thread,
  onSelect,
}: {
  api: AccountApi;
  refId: string;
  thread: { id: string; name: string };
  onSelect: (id: string) => void;
}) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  // Each thread gets its own answer; an answer for a thread picked before is dropped.
  const latest = useRef(0);

  useEffect(() => {
    const mine = ++latest.current;
    setLoad({ kind: "loading" });
    api
      .threadCard!(refId, thread.id)
      .then((card) => {
        if (mine === latest.current) setLoad({ kind: "card", card });
      })
      .catch(() => {
        // a refused or failed sheet: nothing is shown, and the pane is as it was
        if (mine === latest.current) setLoad({ kind: "none" });
      });
  }, [api, refId, thread.id]);

  if (load.kind === "none") return null;
  if (load.kind === "loading") {
    return (
      <section class="stack" aria-label={thread.name}>
        <p class="small muted" role="status">{w.loading}</p>
      </section>
    );
  }
  const card = load.card;

  const personLine = (p: { id: string; name: string; role: string }) => {
    if (p.role === "quest giver") return <>{w.giver("")}<Name id={p.id} name={p.name} onSelect={onSelect} /></>;
    if (p.role === "takes it on") return <>{w.takesOn("")}<Name id={p.id} name={p.name} onSelect={onSelect} /></>;
    if (p.role === "target") return <>{w.target("")}<Name id={p.id} name={p.name} onSelect={onSelect} /></>;
    if (p.role === "") return <Name id={p.id} name={p.name} onSelect={onSelect} />;
    return <>{w.otherRole(p.role, "")}<Name id={p.id} name={p.name} onSelect={onSelect} /></>;
  };

  const rewardLine = (p: ThreadCardPart): string => {
    const extras: string[] = [];
    if (p.due === "up_front") extras.push(w.upFront);
    if (p.due === "afterwards") extras.push(w.afterwards);
    if (p.owed === 0) extras.push(w.nothingOwed);
    else if (p.owed !== null && p.owed > 0) extras.push(w.stillOwed(p.owed, p.unit));
    if (p.state === "settled") extras.push(w.settled);
    if (p.state === "wont_be_given") extras.push(w.wontBeGiven);
    if (p.state === "turned_down") extras.push(w.turnedDown);
    const first = p.amount !== null ? w.rewardGiven(p.words) : w.rewardGift(p.words);
    return [first, ...extras].join(", ");
  };
  const helpSuffix: Record<string, string> = { to_keep: w.toKeep, lent: w.lent, returned: w.returned, not_returned: w.notReturned };
  const rewards = card.parts.filter((p) => p.kind === "reward");
  const helps = card.parts.filter((p) => p.kind === "help");
  const steps = card.parts.filter((p) => p.kind === "objective" || p.kind === "complication" || p.kind === "time_limit");

  const stepLine = (p: ThreadCardPart): string => {
    if (p.kind === "objective") return `${p.words}${w.stepState[p.state] ?? ""}`;
    if (p.kind === "complication") return `${w.inTheWay}${p.words}${p.state === "overcome" ? w.overcome : p.state === "no_longer_matters" ? w.noLongerMatters : ""}`;
    const end = p.dated ? w.dated : "";
    const state = p.state === "met" ? w.met : p.state === "lifted" ? w.lifted : "";
    return `${w.timeLimit}${p.words}${end}${state}`;
  };

  return (
    <section class="stack" aria-label={card.thread.name}>
      <p><strong>{card.thread.kind} · {card.thread.stateWords}</strong></p>

      <h3 class="small">{w.aboutHeading}</h3>
      {card.thread.description ? (
        <p class="small">{card.thread.description}</p>
      ) : (
        <p class="small">
          {w.noSummary}{" "}
          <a href={`/account/names/${refId}#said=${encodeURIComponent(card.thread.name)}`}>{w.writeOne}</a>
        </p>
      )}

      {card.people.length > 0 && (
        <>
          <h3 class="small">{w.peopleHeading}</h3>
          <ul class="stack small">
            {card.people.map((p) => (
              <li key={`${p.id}:${p.role}`}>{personLine(p)}</li>
            ))}
          </ul>
        </>
      )}

      <h3 class="small">{w.rewardHeading}</h3>
      {rewards.length === 0 && helps.length === 0 && <p class="small">{w.noReward}</p>}
      <ul class="stack small">
        {rewards.map((p) => (
          <li key={p.id}>{rewardLine(p)}</li>
        ))}
      </ul>
      {helps.length > 0 && (
        <>
          <p class="small">{w.helpLead}</p>
          <ul class="stack small">
            {helps.map((p) => (
              <li key={p.id}>{`${p.words} ${helpSuffix[p.state] ?? ""}`.trim()}</li>
            ))}
          </ul>
        </>
      )}

      {steps.length > 0 && (
        <>
          <h3 class="small">{w.stepsHeading}</h3>
          {card.of > 0 && <p class="small">{w.doneOf(card.done, card.of)}</p>}
          <ul class="stack small">
            {steps.map((p) => (
              <li key={p.id} style={p.underId ? { paddingLeft: "1em" } : undefined}>{stepLine(p)}</li>
            ))}
          </ul>
        </>
      )}

      {card.waiting > 0 && (
        <p class="small">
          <a href="/account/questions">{w.questionsWaiting(card.waiting)}</a>
        </p>
      )}
    </section>
  );
}

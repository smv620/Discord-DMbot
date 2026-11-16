/** @jsxImportSource preact */
// The edit controls under a kept thread's quest sheet (#4562, slice 2 of #4476): a DM changes a part's state, shows or takes it off, fixes
// its words, and adds a step, reward, help or time limit. Each press is one change for the bot, waited for and read again, with Undo for
// the last change. DM only, behind QUEST_EDIT_LIVE. Words only: no id is ever shown.
import { useRef, useState } from "preact/hooks";

import { questEditWords as w } from "../content/quest-edit-words";
import { ApiError, type AccountApi, type ThreadCard, type ThreadCardPart, type ThreadPartsChange } from "./api";

type Said = { ok: boolean; text: string; batch: number | null };

export default function QuestSheetEdit({
  api,
  refId,
  threadId,
  card,
  reload,
}: {
  api: AccountApi;
  refId: string;
  threadId: string;
  card: ThreadCard;
  reload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  const flight = useRef(false);
  const [kind, setKind] = useState("");
  const [addWords, setAddWords] = useState("");
  const [addState, setAddState] = useState("to_keep");
  const [addDue, setAddDue] = useState("");
  const [addLimit, setAddLimit] = useState("");

  // One press at a time: the flight ref stops a second press while one waits (as World notes does).
  async function press(change: ThreadPartsChange, done: (batch: number | null) => string): Promise<void> {
    if (flight.current || !api.changeThreadParts) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const made = await api.changeThreadParts(refId, threadId, change);
      setSaid({ ok: true, text: done(made.batch), batch: made.batch });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "";
      const text = kind === "gone" ? w.gone : kind === "no-clock" ? w.noClock : w.failed;
      setSaid({ ok: false, text, batch: null });
    } finally {
      flight.current = false;
      setBusy(false);
      await reload();
    }
  }

  const undo = (batch: number) =>
    press({ action: "undo", batch: String(batch) }, () => w.undone);

  const stateChoices = (p: ThreadCardPart): string[] => {
    const all = Object.keys(w.states[p.kind] ?? {});
    return all.filter((s) => s !== p.state);
  };

  const partLabel = (p: ThreadCardPart): string => p.words;

  const addKind = w.kinds.find((k) => k.kind === kind);
  const addWordsOk = addWords.trim().length > 0 && addWords.trim().length <= 200;
  const addSend = (): void => {
    if (!addKind || !addWordsOk) return;
    const change: ThreadPartsChange = { action: "add", kind: addKind.kind, words: addWords.trim() };
    if (addKind.kind === "help") change.state = addState;
    if (addKind.kind === "reward" && addDue) change.due = addDue as "up_front" | "afterwards";
    if (addKind.kind === "time_limit" && addLimit) change.limit = addLimit as "hard" | "soft";
    void press(change, () => w.added(addWords.trim()));
    setAddWords("");
  };

  return (
    <section class="stack" aria-label={w.change}>
      {card.parts.map((p) => (
        <PartControls
          key={p.id}
          p={p}
          busy={busy}
          stateChoices={stateChoices(p)}
          onState={(s) => void press({ action: "state", part: p.id, state: s }, () => w.stateSaid(s, partLabel(p)))}
          onShow={(shown) => void press({ action: "show", part: p.id, shown }, () => (shown ? w.shownSaid(partLabel(p)) : w.hiddenSaid(partLabel(p))))}
          onRemove={() => void press({ action: "remove", part: p.id }, () => w.removedSaid(partLabel(p)))}
          onFix={(value, storyChanged) =>
            void press({ action: "fix", part: p.id, field: storyChanged ? "words_story" : "words", value }, () => w.fixedSaid(value))
          }
          onDate={(days) =>
            void press({ action: "date", part: p.id, days }, () => (days === null ? w.dateCleared(partLabel(p)) : w.dateSet(partLabel(p))))
          }
        />
      ))}

      <details>
        <summary>{w.addToThread}</summary>
        <label class="field">
          {w.whatToAdd}
          <select
            value={kind}
            disabled={busy}
            onChange={(e) => {
              // a choice made for one kind does not carry over to the next
              setKind((e.target as HTMLSelectElement).value);
              setAddState("to_keep");
              setAddDue("");
              setAddLimit("");
            }}
          >
            <option value="">—</option>
            {w.kinds.map((k) => (
              <option key={k.kind} value={k.kind}>{k.label}</option>
            ))}
          </select>
        </label>
        {addKind && (
          <div class="stack">
            <label class="field">
              {addKind.fieldLabel}
              <input type="text" maxLength={200} placeholder={addKind.placeholder} value={addWords} disabled={busy} onInput={(e) => setAddWords((e.target as HTMLInputElement).value)} />
            </label>
            {addKind.kind === "reward" && (
              <label class="field">
                {w.whenGiven}
                <select value={addDue} disabled={busy} onChange={(e) => setAddDue((e.target as HTMLSelectElement).value)}>
                  <option value="">{w.notSaid}</option>
                  <option value="up_front">{w.upFront}</option>
                  <option value="afterwards">{w.afterwards}</option>
                </select>
              </label>
            )}
            {addKind.kind === "help" && (
              <label class="field">
                {w.isItTheirs}
                <select value={addState} disabled={busy} onChange={(e) => setAddState((e.target as HTMLSelectElement).value)}>
                  <option value="to_keep">{w.theirsToKeep}</option>
                  <option value="lent">{w.lentBack}</option>
                </select>
              </label>
            )}
            {addKind.kind === "time_limit" && (
              <label class="field">
                {w.whenMissed}
                <select value={addLimit} disabled={busy} onChange={(e) => setAddLimit((e.target as HTMLSelectElement).value)}>
                  <option value="">{w.notSaid}</option>
                  <option value="hard">{w.hard}</option>
                  <option value="soft">{w.soft}</option>
                </select>
              </label>
            )}
            <button type="button" class="button secondary" disabled={busy || !addWordsOk} onClick={addSend}>{w.add}</button>
          </div>
        )}
      </details>

      {busy && <p class="small muted" role="status">{w.working}</p>}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {said.ok && said.batch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void undo(said.batch!)}>
              {w.undo}
            </button>
          )}
        </p>
      )}
    </section>
  );
}

function PartControls({
  p,
  busy,
  stateChoices,
  onState,
  onShow,
  onRemove,
  onFix,
  onDate,
}: {
  p: ThreadCardPart;
  busy: boolean;
  stateChoices: string[];
  onState: (state: string) => void;
  onShow: (shown: boolean) => void;
  onRemove: () => void;
  onFix: (value: string, storyChanged: boolean) => void;
  /** Sets or changes a time limit's date (N game days from now), or clears it with null (#4583). */
  onDate: (days: number | null) => void;
}) {
  const [text, setText] = useState(p.words);
  const [storyChanged, setStoryChanged] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);
  const [otherOpen, setOtherOpen] = useState(false);
  const [days, setDays] = useState("");
  // A whole number of game days from 1 to 3650: anything else (0, 2.5, empty) is not sent.
  const daysOk = /^\d+$/.test(days) && Number(days) >= 1 && Number(days) <= 3650;
  const isTime = p.kind === "time_limit";
  const fixOk = text.trim().length > 0 && text.trim().length <= 200 && text.trim() !== p.words;
  const hasStates = p.kind !== "reward_given" && stateChoices.length > 0;
  return (
    <div class="stack">
      <p class="small">{p.words}</p>
      <details>
      <summary>{w.change}</summary>
      <p class="row">
        {hasStates &&
          stateChoices.map((s) => (
            <button key={s} type="button" class="button secondary" disabled={busy} onClick={() => onState(s)}>
              {w.states[p.kind]?.[s] ?? s}
            </button>
          ))}
        {p.tableKnows !== undefined && (
          <button type="button" class="button secondary" disabled={busy} onClick={() => onShow(!p.tableKnows)}>
            {p.tableKnows ? w.hideFromPlayers : w.showToPlayers}
          </button>
        )}
        <button type="button" class="button secondary" disabled={busy} onClick={onRemove}>{w.takeItOff}</button>
      </p>
      {isTime && (
        <div class="stack">
          <p class="row">
            {p.dated ? (
              <>
                <button type="button" class="button secondary" disabled={busy} onClick={() => setDateOpen(!dateOpen)}>{w.changeDate}</button>
                <button type="button" class="button secondary" disabled={busy} onClick={() => onDate(null)}>{w.clearDate}</button>
              </>
            ) : (
              <button type="button" class="button secondary" disabled={busy} onClick={() => setDateOpen(!dateOpen)}>{w.setDate}</button>
            )}
          </p>
          {dateOpen && (
            <div class="stack">
              <p class="row">
                {w.dateChoices.map((choice) => (
                  <button key={choice.days} type="button" class="button secondary" disabled={busy} onClick={() => onDate(choice.days)}>
                    {choice.label}
                  </button>
                ))}
                <button type="button" class="link" disabled={busy} onClick={() => setOtherOpen(true)}>{w.otherDays}</button>
              </p>
              {otherOpen && (
                <p class="row">
                  <label class="field">
                    {w.numberOfDays}
                    <input type="number" min={1} max={3650} step={1} value={days} disabled={busy} onInput={(e) => setDays((e.target as HTMLInputElement).value)} />
                  </label>
                  <button type="button" class="button secondary" disabled={busy || !daysOk} onClick={() => onDate(Number(days))}>{w.setTheDate}</button>
                </p>
              )}
            </div>
          )}
        </div>
      )}
      <div class="stack">
        <label class="field">
          {w.newWords}
          <input type="text" maxLength={200} value={text} disabled={busy} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        </label>
        <label class="row">
          <input type="checkbox" checked={storyChanged} disabled={busy} onChange={(e) => setStoryChanged((e.target as HTMLInputElement).checked)} />
          {w.storyChanged}
        </label>
        <button type="button" class="button secondary" disabled={busy || !fixOk} onClick={() => onFix(text.trim(), storyChanged)}>{w.save}</button>
      </div>
      </details>
    </div>
  );
}

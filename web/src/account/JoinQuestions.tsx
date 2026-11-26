/** @jsxImportSource preact */
// The join question on the Questions page (#4778 part b): "What does Mira know about what came before?", one message at a time,
// with "Only what everyone knows" and "What the party knows" (and ↩ Undo after the latter), as Discord's card does. The words are
// the API's rules, shown as text only. DM only: the door answers anyone else with nothing.
import { useEffect, useRef, useState } from "preact/hooks";

import { joinWords as w } from "../content/join-questions-words";
import { ApiError, type AccountApi, type JoinGroup } from "./api";
import SavingWait from "./SavingWait";
import LazyPanel from "./LazyPanel";
import { joinChooseWords as cw } from "../content/join-choose-words";

// The "Let me choose" page is loaded only once the DM opens it (#4891).
const loadJoinChoose = () => import("./JoinChoose");

/** The plain line for an answer that did not go through, by the bot's error word. */
function sayFailure(error: unknown, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.gone;
  // The bot's own words for an Undo that is too late or whose message changed since (waitForAnswer maps them to these kinds).
  if (undoing && (error.kind === "undo-late" || error.kind === "changed")) return w.tooLate;
  return w.couldNot;
}

export default function JoinQuestions({
  api,
  campaignRef,
  id,
  onLeft,
}: {
  api: AccountApi;
  campaignRef: string;
  id: string;
  onLeft?: (left: number) => void;
}) {
  const [groups, setGroups] = useState<JoinGroup[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // The last "What the party knows" with its change number, and the names it was for (the card may show another message by then).
  const [undo, setUndo] = useState<{ batch: number; names: string[]; back: boolean } | null>(null);
  const flight = useRef(false);
  // "Let me choose" (#4891): the open page and its question. Held here, so it survives the question leaving the list after a pick.
  const [choosing, setChoosing] = useState<{ group: string; names: string[]; back: boolean } | null>(null);
  const chooseButton = useRef<HTMLButtonElement | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.joinQuestions) return;
    try {
      const got = await api.joinQuestions(campaignRef);
      if (live.current) (setGroups(got), setFailed(false), onLeft?.(got.length));
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function press(run: () => Promise<void>, undoing = false): Promise<void> {
    if (!api.answerJoin || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, undoing) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  const everyone = (g: JoinGroup) =>
    press(async () => {
      await api.answerJoin!(campaignRef, { action: "everyone", group: g.group });
      if (live.current) setSaid({ ok: true, text: g.kind === "back" ? w.backEveryoneDone(g.names) : w.everyoneDone(w.join(g.names), g.names.length === 1) });
    });
  const party = (g: JoinGroup) =>
    press(async () => {
      const got = await api.answerJoin!(campaignRef, { action: "party", group: g.group });
      if (!live.current) return;
      const names = w.join(g.names);
      const one = g.names.length === 1;
      if (got.batch === null) {
        setSaid({ ok: true, text: w.nothingToGive(names) });
        return;
      }
      setSaid({ ok: true, text: got.given === null ? w.nothingToGive(names) : w.partyDone(names, got.given, one) });
      setUndo({ batch: got.batch, names: g.names, back: g.kind === "back" });
    });
  const takeBack = (back: { batch: number; names: string[]; back: boolean }) =>
    press(async () => {
      await api.answerJoin!(campaignRef, { action: "undo", batch: back.batch });
      if (live.current)
        (setUndo(null), setSaid({ ok: true, text: back.back ? w.backUndone(back.names) : w.undone(w.join(back.names), back.names.length === 1) }));
    }, true);

  if (failed && !groups) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.tryAgain}
        </button>
      </div>
    );
  }
  if (!groups) return <p class="muted" role="status">{w.loading}</p>;
  const g = groups[0];
  const names = g ? w.join(g.names) : "";
  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>{w.title}</h3>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">{said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>
      {g ? (
        <>
          <p>
            <strong>{g.kind === "back" ? w.backHeading(g.names) : w.heading(g.names)}</strong>
          </p>
          <p>{g.kind === "back" ? w.backQuestion(g.names) : w.question(g.names)}</p>
          <p class="small">{g.kind === "back" ? w.backExplain(g.names) : w.explain(g.names)}</p>
          <p class="small">{w.later(names)}</p>
          {g.answer === "everyone" && <p class="small">{w.everyoneChosen}</p>}
          <div class="row">
            {g.answer !== "everyone" && (
              <button type="button" class="button secondary" disabled={busy} onClick={() => void everyone(g)}>
                {g.kind === "back" ? w.backEveryone : w.everyone}
              </button>
            )}
            <button type="button" class="button" disabled={busy} onClick={() => void party(g)}>
              {w.party}
            </button>
          </div>
          {undo && (
            <div class="stack">
              <p class="small muted">{w.undoFor(undo.names)}</p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack(undo)}>
                {w.undo}
              </button>
            </div>
          )}
          {api.joinChoices && (
            <button
              ref={chooseButton}
              type="button"
              class="button secondary"
              aria-expanded={choosing?.group === g.group}
              onClick={() => setChoosing(choosing?.group === g.group ? null : { group: g.group, names: g.names, back: g.kind === "back" })}
            >
              {cw.button}
            </button>
          )}
          <SavingWait active={busy} stage={w.saving} />
        </>
      ) : null}
      {choosing && api.joinChoices && (
        <LazyPanel
          key={choosing.group}
          load={loadJoinChoose}
          what="the Let me choose page"
          props={{
            api,
            campaignRef,
            group: choosing.group,
            names: choosing.names,
            back: choosing.back,
            onClose: () => {
              setChoosing(null);
              chooseButton.current?.focus();
              void read();
            },
          }}
        />
      )}
    </section>
  );
}

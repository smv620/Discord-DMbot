/** @jsxImportSource preact */
// A player character's party record on its row on the Names page (#4778 a2, the page half of #4798): "In the party since…", the
// stretches it left, "🚪 Has left the party" with why and "🤝 Back in the party", each with ↩️ Undo where the core gives one. Core's
// words, unchanged; only the date is the page's own. DM only; loaded only while PARTY_RECORD_LIVE and opened from the row.
import { useRef, useState } from "preact/hooks";

import { dayOf, partyRecordWords as w } from "../content/party-record-words";
import { ApiError, type AccountApi, type PartyRecordCharacter } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
// What can be undone: a leave, or a return that DMbot asks about (`join`), by its change number and the name it was for.
type Back = { kind: "leave" | "join"; batch: number; name: string };

/** The plain line for an error word from the party record's doors (waitForAnswer maps them to these kinds). */
function sayFailure(error: unknown, name: string, tooLate: string | null): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "not-in-party") return w.notInParty(name);
  if (error.kind === "already-back") return w.alreadyBack(name);
  if (error.kind === "not-a-pc") return w.notAPc(name);
  if (tooLate !== null && (error.kind === "undo-late" || error.kind === "changed")) return tooLate;
  return w.couldNot;
}

export default function PartyRecord({ api, refId, row }: { api: AccountApi; refId: string; row: { id: string; name: string } }) {
  const [open, setOpen] = useState(false);
  const [character, setCharacter] = useState<PartyRecordCharacter | null>(null);
  const [failed, setFailed] = useState(false);
  const [asking, setAsking] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const [back, setBack] = useState<Back | null>(null);
  const flight = useRef(false);

  async function read(): Promise<void> {
    if (!api.partyRecord) return;
    try {
      const all = await api.partyRecord(refId);
      setCharacter(all.find((c) => c.id === row.id) ?? null);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }

  // Read only when opened from the row.
  function toggle(): void {
    const next = !open;
    setOpen(next);
    if (next) void read();
  }

  async function run(name: string, tooLate: string | null, act: () => Promise<void>): Promise<void> {
    if (flight.current) return; // a second press while one waits sends nothing
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await act();
    } catch (error) {
      setSaid({ ok: false, text: sayFailure(error, name, tooLate) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      setBusy(false);
    }
  }

  const leave = (why: "left" | "retired" | "died" | "away") =>
    run(row.name, null, async () => {
      setAsking(false);
      const got = await api.partyLeave!(refId, row.id, why);
      setSaid({ ok: true, text: w.leftDone(row.name, why) });
      setBack(got.batch === null ? null : { kind: "leave", batch: got.batch, name: row.name });
    });

  const returnToParty = () =>
    run(row.name, null, async () => {
      const got = await api.partyBack!(refId, row.id);
      setSaid({ ok: true, text: w.backDone(row.name, got.asked ?? "none") });
      setBack(got.asked === "join" && got.batch !== null ? { kind: "join", batch: got.batch, name: row.name } : null);
    });

  const takeBack = (undo: Back) =>
    run(undo.name, undo.kind === "join" ? w.tooLateBack(undo.name) : w.tooLateLeave, async () => {
      await api.partyUndo!(refId, undo.batch);
      setBack(null);
      setSaid({ ok: true, text: undo.kind === "leave" ? w.undoneLeave(undo.name) : w.undoneBack(undo.name) });
    });

  return (
    <div class="stack">
      <button type="button" class="button secondary" aria-expanded={open} onClick={toggle}>
        {w.open}
      </button>
      {open && failed && !character && (
        <p class="warn" role="alert">
          {w.failed}
        </p>
      )}
      {open && character && (
        <section class="stack" aria-label={w.heading}>
          <p class={said?.ok ? "ok" : "visually-hidden"} role="status">{said?.ok ? said.text : ""}</p>
          <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>
          {character.spans.map((s, i) => (
            <div key={i} class="stack small">
              <p>{w.since(dayOf(s.from), s.session)}</p>
              {s.to !== null && <p>{w.leftOn(dayOf(s.to), s.ended)}</p>}
            </div>
          ))}
          {character.inParty && !asking && (
            <p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => setAsking(true)}>
                {w.leaveButton}
              </button>
            </p>
          )}
          {character.inParty && asking && (
            <div class="stack" role="group" aria-label={w.why(row.name)}>
              <p>
                <strong>{w.why(row.name)}</strong> {w.nothingForgotten(row.name)}
              </p>
              <div class="row">
                {(["left", "retired", "died", "away"] as const).map((why) => (
                  <button key={why} type="button" class="button secondary" disabled={busy} onClick={() => void leave(why)}>
                    {w.reasons[why]}
                  </button>
                ))}
              </div>
            </div>
          )}
          {!character.inParty && (
            <p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => void returnToParty()}>
                {w.backButton}
              </button>
            </p>
          )}
          {back && (
            <p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack(back)}>
                {w.undo}
              </button>
            </p>
          )}
          <SavingWait active={busy} stage={w.saving} />
        </section>
      )}
    </div>
  );
}

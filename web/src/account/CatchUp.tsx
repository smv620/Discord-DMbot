/** @jsxImportSource preact */
// "Catch up" on a name's row (#4793, the page half of #4824): who is behind the rest of the party on this entry, and a DM's one press
// to tell them everything on the card that the rest of the party knows, with ↩️ Undo. Its own file, loaded only from the row while
// CATCH_UP_LIVE. DM only; nothing is drawn when nobody is behind.
import { useRef, useState } from "preact/hooks";

import { catchUpWords as w } from "../content/catch-up-words";
import { ApiError, type AccountApi, type CatchUpChoice } from "./api";
import SavingWait from "./SavingWait";

type Member = { id: string; name: string };
type Said = { ok: boolean; text: string };

/** The plain line for a catch-up that did not go through. */
function sayFailure(error: unknown, name: string, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "nothing-left") return w.nothingLeft(name);
  if (error.kind === "not-in-party") return w.notInParty(name);
  if (undoing && (error.kind === "undo-late" || error.kind === "changed")) return w.tooLate(name);
  return w.couldNot;
}

export default function CatchUp({ api, refId, entry }: { api: AccountApi; refId: string; entry: string }) {
  const [open, setOpen] = useState(false);
  const [choices, setChoices] = useState<CatchUpChoice[] | null>(null);
  const [more, setMore] = useState(0);
  const [members, setMembers] = useState<Member[]>([]);
  const [picking, setPicking] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const [undo, setUndo] = useState<{ batch: number; name: string; told: number } | null>(null);
  const flight = useRef(false);

  // Who is behind, and the names from the party list; a choice whose id has no name is dropped.
  async function read(): Promise<void> {
    if (!api.catchUpChoices) return;
    try {
      const [got, party] = await Promise.all([api.catchUpChoices(refId, entry), api.partyRecord ? api.partyRecord(refId) : Promise.resolve([])]);
      const named = party.map((p) => ({ id: p.id, name: p.name }));
      setMembers(named);
      setChoices(got.choices.filter((c) => named.some((m) => m.id === c.id)));
      setMore(got.more);
    } catch {
      setChoices([]);
    }
  }

  function toggle(): void {
    const next = !open;
    setOpen(next);
    setPicking(false);
    if (next) void read();
  }

  const nameOf = (id: string): string => members.find((m) => m.id === id)?.name ?? "";

  async function run(name: string, undoing: boolean, act: () => Promise<void>): Promise<void> {
    if (flight.current) return; // a second press while one waits sends nothing
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await act();
    } catch (error) {
      setSaid({ ok: false, text: sayFailure(error, name, undoing) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      setBusy(false);
    }
  }

  const catchOne = (c: CatchUpChoice) =>
    run(nameOf(c.id), false, async () => {
      setPicking(false);
      const got = await api.catchUp!(refId, c.id, entry);
      const told = got.told ?? c.count;
      setSaid({ ok: true, text: w.done(nameOf(c.id), told) });
      setUndo(got.batch === null ? null : { batch: got.batch, name: nameOf(c.id), told });
    });

  const takeBack = (u: { batch: number; name: string; told: number }) =>
    run(u.name, true, async () => {
      await api.partyUndo!(refId, u.batch);
      setUndo(null);
      setSaid({ ok: true, text: w.undone(u.name, u.told) });
    });

  if (!open) {
    return (
      <button type="button" class="button secondary" aria-expanded={false} onClick={toggle}>
        {w.manyLabel}
      </button>
    );
  }
  if (!choices || choices.length === 0) {
    return choices === null ? <p class="muted" role="status">{w.saving}</p> : null;
  }

  const single = choices.length === 1 ? choices[0]! : null;
  const names = choices.map((c) => nameOf(c.id));
  return (
    <section class="stack" aria-label={w.manyLabel}>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">{said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>
      {single ? (
        <>
          <p>{w.one(nameOf(single.id), single.count)}</p>
          <p>
            <button type="button" class="button" disabled={busy} onClick={() => void catchOne(single)}>
              {w.oneLabel(nameOf(single.id))}
            </button>
          </p>
        </>
      ) : picking ? (
        <div class="stack" role="group" aria-label={w.pickQuestion}>
          <p>{w.pickQuestion}</p>
          <div class="row">
            {choices.map((c) => (
              <button key={c.id} type="button" class="button secondary" disabled={busy} onClick={() => void catchOne(c)}>
                {w.pickLabel(nameOf(c.id), c.count)}
              </button>
            ))}
            <button type="button" class="button secondary" disabled={busy} onClick={() => setPicking(false)}>
              {w.back}
            </button>
          </div>
        </div>
      ) : (
        <>
          <p>{w.many(names)}</p>
          <p>
            <button type="button" class="button" disabled={busy} onClick={() => setPicking(true)}>
              {w.manyLabel}
            </button>
          </p>
        </>
      )}
      {more > 0 && <p class="small">{more === 1 ? w.moreOne : w.moreMany(more)}</p>}
      {undo && (
        <p>
          <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack(undo)}>
            {w.undo}
          </button>
        </p>
      )}
      <SavingWait active={busy} stage={w.saving} />
    </section>
  );
}

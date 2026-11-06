/** @jsxImportSource preact */
// "Line for the players" on a name of the Names page (#3568, the website half of #2967): the DM writes, changes or clears
// the one short line players are told about this name in recaps and answers. A separate file, fetched only when a DM opens a
// name to change it. The save goes through the page's own `act` (so the list is read again, an Undo chance ends, a signed-out
// DM is sent to sign in and a name that has gone closes the editor); one press at a time. The line is shown back exactly as
// saved, so a secret in it can be spotted. DM only, and only while PLAYERS_LINE_LIVE (the Names page decides both).
import { useEffect, useRef, useState } from "preact/hooks";

import { PLAYERS_LINE_MAX, playersLineWords as w } from "../content/players-line-words";
import type { NameChange, NameRow } from "./api";

export default function PlayersLine({ row, act }: { row: NameRow; act: (change: NameChange, ok: string) => Promise<string | null> }) {
  // What is saved now: the page's value, then what this box saved last.
  const [saved, setSaved] = useState(row.playersText ?? "");
  const [text, setText] = useState(saved);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const tidy = (value: string): string => value.split(/\s+/).filter(Boolean).join(" ");
  const clean = tidy(text);
  // The box follows the row when a read changes it, but never overwrites what is being typed.
  useEffect(() => {
    const next = row.playersText ?? "";
    setText((now) => (tidy(now) === saved ? next : now));
    setSaved(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.id, row.playersText]);
  const id = `players-line-${row.id}`;

  async function save(): Promise<void> {
    if (flight.current) return;
    if (clean.length > PLAYERS_LINE_MAX) return setNote({ ok: false, text: w.tooLong });
    flight.current = true;
    setBusy(true);
    setNote(null);
    const said = clean === "" ? w.removed : w.saved(clean);
    const problem = await act({ action: "players-line", line: clean }, said);
    flight.current = false;
    setBusy(false);
    if (problem === null) {
      setSaved(clean);
      setText(clean);
      setNote({ ok: true, text: said });
    } else if (problem !== "") {
      setNote({ ok: false, text: problem });
    }
  }

  return (
    <form
      class="name-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label class="field" for={id}>
        {w.label}
      </label>
      <input
        id={id}
        type="text"
        value={text}
        aria-describedby={`${id}-hint`}
        readOnly={busy}
        onInput={(e) => {
          setText((e.target as HTMLInputElement).value);
          setNote(null);
        }}
      />
      <p class="small muted" id={`${id}-hint`}>
        {w.hint} {w.count(clean.length)}
      </p>
      <button type="submit" class="button secondary" disabled={busy || clean === saved}>
        {busy ? w.saving : w.save}
      </button>
      <p class={note ? (note.ok ? "ok small" : "warn small") : "visually-hidden"} role="status">
        {note?.text ?? ""}
      </p>
    </form>
  );
}

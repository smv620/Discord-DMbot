/** @jsxImportSource preact */
// The "who plays this character" picker of the wizard's step 3 (#4204). It looks and works like a picker: a down-arrow button, the list opens
// on focus or click, typing filters it, the arrow keys move, Enter picks and Escape closes. "Not set yet" is the first choice; members who
// played with this DM here before come first, marked. Text typed that matches no member is never removed: it stays in the box with a line
// saying so, and the character stays "not set yet". No Discord id is ever shown or typed: only names.
import { useId, useMemo, useState } from "preact/hooks";

import { wizardWords as w } from "../content/wizard-words";
import type { WizardMember } from "./wizard-api";

const PENDING = "pending";

const ELSEWHERE = "elsewhere";

/** `elsewhere`: a last row for someone who is not in the list ("Someone else: type their Discord ID…", #4225); choosing it calls
 * `onChoose` and picks nobody, so the caller can show a box for the ID. */
export default function PlayerPicker({ label, members, value, onPick, elsewhere }: { label: string; members: WizardMember[]; value: string; onPick(id: string): void; elsewhere?: { label: string; onChoose(): void } }) {
  const t = w.players;
  const id = useId();
  const chosen = value === PENDING ? undefined : members.find((m) => m.userId === value);
  // What the box shows: what was typed, else the chosen member's name ("Picked earlier" when the list no longer has them).
  const [typed, setTyped] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const shown = typed ?? (value === PENDING ? "" : (chosen?.name ?? t.pickedEarlier));
  const needle = (typed ?? "").trim().toLowerCase();
  const rows = useMemo(() => {
    const matching = members.filter((m) => needle === "" || m.name.toLowerCase().includes(needle));
    const last = elsewhere ? [{ userId: ELSEWHERE, name: elsewhere.label, before: false }] : [];
    return [{ userId: PENDING, name: t.pending, before: false }, ...matching, ...last];
  }, [members, needle, t.pending, elsewhere?.label]);
  const unmatched = typed !== null && needle !== "" && !members.some((m) => m.name.toLowerCase() === needle) && rows.length === (elsewhere ? 2 : 1);

  function pick(row: WizardMember): void {
    setTyped(null);
    setOpen(false);
    setActive(0);
    if (row.userId === ELSEWHERE) elsewhere?.onChoose();
    else onPick(row.userId);
  }
  function key(e: KeyboardEvent): void {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(rows.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      const row = rows[active];
      if (row) pick(row);
    } else if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
    }
  }
  return (
    <div class="picker">
      <label for={`${id}-box`}>{label}</label>
      <div class="row">
        <input
          id={`${id}-box`}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={open ? `${id}-${active}` : undefined}
          aria-describedby={unmatched ? `${id}-note` : undefined}
          autoComplete="off"
          placeholder={t.placeholder}
          value={shown}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onInput={(e) => {
            setTyped((e.target as HTMLInputElement).value);
            setOpen(true);
            setActive(0);
          }}
          onKeyDown={key}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
        />
        <button type="button" class="button secondary" aria-label={t.open} tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => setOpen((o) => !o)}>
          <span aria-hidden="true">▾</span>
        </button>
      </div>
      {open && (
        <ul id={`${id}-list`} role="listbox" aria-label={t.listLabel(label)} class="stack">
          {rows.map((m, i) => (
            <li key={m.userId} id={`${id}-${i}`} role="option" aria-selected={i === active} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(m)}>
              {m.name}
              {m.before && <span class="small muted"> · {t.before}</span>}
            </li>
          ))}
        </ul>
      )}
      {unmatched && (
        <p class="small" id={`${id}-note`} role="status">
          {t.noMatch(shown.trim())}
        </p>
      )}
    </div>
  );
}

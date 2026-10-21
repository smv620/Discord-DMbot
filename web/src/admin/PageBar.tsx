/** @jsxImportSource preact */
// The page bar under a paged list on the admin page (#2199, shared in #2211): « Newest, ‹ Newer,
// "Page [n] of N" with Go, Older ›, Oldest ». One component for Recent changes and for the support
// tickets, so they look and behave the same. It owns the jump box and what it accepts (whole
// numbers from 1 to the last page); the list itself, and what a page change reads, stay with the
// owner, which says whether the move worked.
import { useRef, useState } from "preact/hooks";

import { text } from "../content/admin";

interface Props {
  /** Makes the box's id unique when more than one bar is on a page. */
  id: string;
  /** Names this bar for a screen reader, so two bars on one page can be told apart. */
  label: string;
  page: number;
  pages: number;
  /** Reading a page is under way: the Go button waits. */
  busy: boolean;
  /** News the owner has (a failed read, "that page is gone"): shown in place of the page number. */
  notice: string | null;
  /** Asks for a page. Resolves with the page that is showing afterwards (the one asked for, or
   * the old one if the read failed), so the box can settle on it. */
  onGo: (page: number) => Promise<number>;
}

export default function PageBar({ id, label, page, pages, busy, notice, onGo }: Props) {
  const [typed, setTyped] = useState(String(page));
  const [pick, setPick] = useState<string | null>(null);
  // The box follows the page: a move, a jump, or the owner going back to page 1. Done while
  // rendering (not in an effect) so a late effect can never overwrite what was just typed.
  const [seen, setSeen] = useState(page);
  if (seen !== page) {
    setSeen(page);
    setTyped(String(page));
    setPick(null);
  }
  // Counts what the person typed, so a late settling never overwrites newer typing.
  const typing = useRef(0);
  const box = useRef<HTMLInputElement>(null);
  const seenRef = useRef(page);
  seenRef.current = page;

  async function go(wanted: number) {
    setPick(null);
    const edits = typing.current;
    const now = await onGo(wanted);
    // Landing on the first or last page disables the button that was pressed, which drops the
    // keyboard to the top of the page: the box takes it instead.
    // (After the page has drawn, when that button is disabled.)
    setTimeout(() => {
      const active = document.activeElement;
      const lost =
        !active || active === document.body || (active instanceof HTMLButtonElement && active.disabled);
      if (lost) box.current?.focus();
    }, 0);
    // A move that happened is followed by the effect above. One that didn't (a failed read) leaves
    // the typed number behind: the box goes back to the page that is showing, unless the person
    // has typed again meanwhile.
    if (now === seenRef.current && typing.current === edits) setTyped(String(now));
  }

  function jump(event: Event) {
    event.preventDefault();
    const value = typed.trim();
    // Whole numbers only: "2.5", "-1", "1e1" and words are refused before anything is asked.
    if (!/^[0-9]{1,9}$/.test(value) || Number(value) < 1 || Number(value) > pages) {
      setPick(text.pagePick(pages));
      return;
    }
    void go(Number(value));
  }

  const showBar = pages > 1;
  const noteId = `${id}-note`;
  const problem = pick ?? notice;
  return (
    <>
      {showBar && (
        <nav class="page-bar" aria-label={label}>
          <button
            type="button"
            class="button secondary"
            disabled={page <= 1}
            onClick={() => void go(1)}
          >
            <span aria-hidden="true">«</span>
            {text.pageNewest}
          </button>
          <button
            type="button"
            class="button secondary"
            disabled={page <= 1}
            onClick={() => void go(page - 1)}
          >
            <span aria-hidden="true">‹</span>
            {text.pageNewer}
          </button>
          <form class="page-jump" onSubmit={jump} noValidate>
            <label for={id}>{text.pageBox}</label>
            <input
              id={id}
              ref={box}
              type="text"
              inputMode="numeric"
              autocomplete="off"
              value={typed}
              aria-describedby={noteId}
              onInput={(e) => {
                typing.current++;
                setTyped(e.currentTarget.value);
                setPick(null);
              }}
            />
            <span>{text.pageOf(pages)}</span>
            <button type="submit" class="button secondary" disabled={busy}>
              {text.pageGo}
            </button>
          </form>
          <button
            type="button"
            class="button secondary"
            disabled={page >= pages}
            onClick={() => void go(page + 1)}
          >
            {text.pageOlder}
            <span aria-hidden="true">›</span>
          </button>
          <button
            type="button"
            class="button secondary"
            disabled={page >= pages}
            onClick={() => void go(pages)}
          >
            {text.pageOldest}
            <span aria-hidden="true">»</span>
          </button>
        </nav>
      )}
      {/* Two notes that are always on the page, each with one role for good, so a screen reader
          hears the page change (status) and any refusal (alert); roles that change are missed. */}
      <p id={noteId} class="visually-hidden" role="status">
        {showBar ? text.pageNow(page, pages) : ""}
      </p>
      <p class={problem ? "warn small" : "visually-hidden"} role="alert">
        {problem ?? ""}
      </p>
    </>
  );
}

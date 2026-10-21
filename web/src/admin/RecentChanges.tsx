/** @jsxImportSource preact */
// Recent changes on the admin page's free access panel (#2199): a window of fixed height showing
// one page of the log, newest first, with a bar underneath to move between pages. The first page
// comes with the panel's own load, so that stays one call; the others are read when asked for.
// After any change the panel loads again and its `first` page replaces this one, so a new
// entry is always in view.
import { useEffect, useRef, useState } from "preact/hooks";

import { text, who } from "../content/admin";
import { type AdminApi, AdminApiError, type AdminLogEntry } from "./api";

interface Props {
  api: AdminApi;
  /** Page 1, as the panel loaded it. */
  first: AdminLogEntry[];
  /** How many pages the log has; missing from an older API (then no bar). */
  pages: number | undefined;
  /** "Oct 8, 2026": the panel's date words. */
  day: (seconds: number) => string;
  onSignedOut: () => void;
  onOff: () => void;
}

export default function RecentChanges({ api, first, pages, day, onSignedOut, onOff }: Props) {
  const [page, setPage] = useState(1);
  const [entries, setEntries] = useState(first);
  const [total, setTotal] = useState(pages ?? 1);
  const [typed, setTyped] = useState("1");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Only the newest request may set what is shown, so a slow older page can't overwrite it.
  const latest = useRef(0);
  const windowBox = useRef<HTMLDivElement>(null);

  // The panel loaded again (a change was made, or the page opened): back to page 1.
  // (Not on the first run: the panel's page 1 is already what is shown, and an effect that runs
  // late must not cancel a page the person has already asked for.)
  const loaded = useRef({ first, pages });
  useEffect(() => {
    if (loaded.current.first === first && loaded.current.pages === pages) return;
    loaded.current = { first, pages };
    latest.current++;
    setPage(1);
    setEntries(first);
    setTotal(pages ?? 1);
    setTyped("1");
    setProblem(null);
    setBusy(false);
  }, [first, pages]);

  // A new page opens at its top, not partway down where the last one was left.
  useEffect(() => {
    if (windowBox.current) windowBox.current.scrollTop = 0;
  }, [page, entries]);

  async function go(wanted: number, notice: string | null = null) {
    const mine = ++latest.current;
    setProblem(notice);
    setBusy(true);
    try {
      const read = await api.grantsLog?.(wanted);
      if (!read || mine !== latest.current) return;
      setEntries(read.log);
      setPage(read.page);
      setTotal(read.pages);
      setTyped(String(read.page));
    } catch (error) {
      if (mine !== latest.current) return;
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") onSignedOut();
      else if (kind === "off") onOff();
      else if (kind === "stale") {
        // The page is gone (the log shrank): the first one is always there.
        void go(1, text.pageGone);
        return;
      } else setProblem(text.pageDown);
      setTyped(String(page));
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

  function jump(event: Event) {
    event.preventDefault();
    const value = typed.trim();
    // Whole numbers only: "2.5", "-1", "1e1" and words are refused before anything is asked.
    if (!/^[0-9]{1,9}$/.test(value) || Number(value) < 1 || Number(value) > total) {
      setProblem(text.pagePick(total));
      return;
    }
    void go(Number(value));
  }

  const showBar = Boolean(api.grantsLog) && total > 1;
  return (
    <>
      {entries.length === 0 ? (
        <p class="muted">{text.noHistory}</p>
      ) : (
        // A fixed-height window that scrolls inside itself, so the box never grows or shrinks
        // as pages change; focusable so the keyboard can scroll it.
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must take focus
        <div
          class="history-window"
          ref={windowBox}
          tabIndex={0}
          role="region"
          aria-label={text.historyList}
          aria-busy={busy}
        >
          <ul class="history">
            {entries.map((e, i) => (
              <li key={`${page}-${e.at}-${i}`}>
                {text.logLine(day(e.at), e.by, e.action, who(e.name, e.discordId))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {showBar && (
        <nav class="page-bar" aria-label={text.pagesNav}>
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
            <label for="log-page">{text.pageBox}</label>
            <input
              id="log-page"
              type="text"
              inputMode="numeric"
              autocomplete="off"
              value={typed}
              aria-describedby="log-page-note"
              onInput={(e) => {
                setTyped(e.currentTarget.value);
                setProblem(null);
              }}
            />
            <span>{text.pageOf(total)}</span>
            <button type="submit" class="button secondary" disabled={busy}>
              {text.pageGo}
            </button>
          </form>
          <button
            type="button"
            class="button secondary"
            disabled={page >= total}
            onClick={() => void go(page + 1)}
          >
            {text.pageOlder}
            <span aria-hidden="true">›</span>
          </button>
          <button
            type="button"
            class="button secondary"
            disabled={page >= total}
            onClick={() => void go(total)}
          >
            {text.pageOldest}
            <span aria-hidden="true">»</span>
          </button>
        </nav>
      )}
      {/* Always on the page, so a screen reader hears the page change and any refusal. */}
      <p
        id="log-page-note"
        class={problem ? "warn small" : "visually-hidden"}
        role={problem ? "alert" : "status"}
      >
        {problem ?? (showBar ? text.pageNow(page, total) : "")}
      </p>
    </>
  );
}

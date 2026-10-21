/** @jsxImportSource preact */
// Recent changes on the admin page's free access panel (#2199): a window of fixed height showing
// one page of the log, newest first, with a bar underneath to move between pages. The first page
// comes with the panel's own load, so that stays one call; the others are read when asked for.
// After any change the panel loads again and its `first` page replaces this one, so a new
// entry is always in view. The bar itself is the shared PageBar (also used by the tickets).
import { useEffect, useRef, useState } from "preact/hooks";

import { text, who } from "../content/admin";
import { type AdminApi, AdminApiError, type AdminLogEntry } from "./api";
import PageBar from "./PageBar";

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
  // The page showing, for a read that fails to hand back.
  const showing = useRef(1);
  const [entries, setEntries] = useState(first);
  const [total, setTotal] = useState(pages ?? 1);
  const [notice, setNotice] = useState<string | null>(null);
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
    showing.current = 1;
    setEntries(first);
    setTotal(pages ?? 1);
    setNotice(null);
    setBusy(false);
  }, [first, pages]);

  // A new page opens at its top, not partway down where the last one was left.
  useEffect(() => {
    if (windowBox.current) windowBox.current.scrollTop = 0;
  }, [page, entries]);

  async function go(wanted: number, news: string | null = null): Promise<number> {
    const mine = ++latest.current;
    setNotice(news);
    setBusy(true);
    try {
      const read = await api.grantsLog?.(wanted);
      if (!read || mine !== latest.current) return showing.current;
      setEntries(read.log);
      setPage(read.page);
      showing.current = read.page;
      setTotal(read.pages);
      return read.page;
    } catch (error) {
      if (mine !== latest.current) return showing.current;
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") onSignedOut();
      else if (kind === "off") onOff();
      else if (kind === "stale") {
        // The page is gone (the log shrank): the first one is always there.
        return go(1, text.pageGone);
      } else setNotice(text.pageDown);
      return showing.current;
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

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
      {api.grantsLog && (
        <PageBar id="log-page" label={text.pagesNav}
          moves={{
            first: text.pageNewest,
            previous: text.pageNewer,
            next: text.pageOlder,
            last: text.pageOldest,
          }}
          page={page} pages={total} busy={busy} notice={notice} onGo={go} />
      )}
    </>
  );
}

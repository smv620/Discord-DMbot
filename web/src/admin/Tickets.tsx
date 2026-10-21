/** @jsxImportSource preact */
// The admin page's support tickets panel (#1340): private messages from people with an account,
// newest first, with Mark answered, Close and Reopen. The words are only ever shown here, to
// the signed-in admin; the API checks the session and the CSRF token on every change.
import { useEffect, useRef, useState } from "preact/hooks";

import { text, waitingFor, weekLine, who } from "../content/admin";
import { planName } from "../content/account";
import { type PlanId } from "../content/pricing";
import {
  type AdminApi,
  AdminApiError,
  type SupportTicketRow,
  type TicketReply,
  type TicketCounts,
  type TicketFilter,
  type TicketStatus,
  type TicketWeek,
} from "./api";
import PageBar from "./PageBar";

/** The filter buttons in the order the team needs them: what still needs a reply first. */
const FILTERS: TicketFilter[] = ["escalated", "new", "answered", "closed", "all"];

interface Props {
  api: AdminApi;
  csrf: string;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
  /** The admin page was switched off while open: the page shows that. */
  onOff: () => void;
}

/** The tickets in the order to read them: "Needs the team" oldest first, so the one that has waited
 * longest is on top (#1485); every other filter keeps the API's order (newest first). */
function shown(rows: SupportTicketRow[], filter: TicketFilter): SupportTicketRow[] {
  if (filter !== "escalated") return rows;
  return [...rows].sort((a, b) => waitingSince(a) - waitingSince(b) || a.id - b.id);
}

/** When the team's wait began (#1494): the hand-over time when the API says it, else the ticket's
 * own time (an older API, or a ticket that was never handed over). Seconds. */
export function waitingSince(ticket: Pick<SupportTicketRow, "at" | "handedOverAt">): number {
  const handed = ticket.handedOverAt;
  return typeof handed === "number" && Number.isFinite(handed) ? handed : ticket.at;
}

/** "Oct 8, 2026, 14:05 UTC": the API stores Unix seconds. */
function when(seconds: number): string {
  return `${new Date(seconds * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  })} UTC`;
}

/** The plan's name, or its id if the site doesn't know it (a plan removed since). */
function plan(id: string): string {
  try {
    return planName(id as PlanId);
  } catch {
    return id;
  }
}

const REPLY_MAX = 4000;

/** The thread after the first message: who wrote each, when, and the words (admin only). */
function Thread({ replies }: { replies: TicketReply[] }) {
  if (replies.length === 0) return null;
  return (
    <div class="thread">
      <h4>{text.threadHeading}</h4>
      <ul class="small">
        {replies.map((r) => (
          <li key={r.id} data-who={r.who}>
            <strong>{text.threadWho[r.who] ?? r.who}</strong>{" "}
            <span class="muted">{when(r.at)}</span>
            <p class="ticket-text">{r.text}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The team's reply to one ticket: a box, one Send, and a plain result. Disabled while sending, so
 * a second tap cannot send it twice; the words stay if it fails. */
function ReplyBox({
  ticket,
  api,
  csrf,
  onSent,
  onSignedOut,
  onOff,
}: {
  ticket: SupportTicketRow;
  api: AdminApi;
  csrf: string;
  onSent: () => void;
  onSignedOut: () => void;
  onOff: () => void;
}) {
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [news, setNews] = useState<{ text: string; ok: boolean } | null>(null);
  const sending = useRef(false);
  const box = useRef<HTMLTextAreaElement>(null);
  if (!ticket.email) return <p class="small muted">{text.replyNoEmail}</p>;
  const id = `reply-${ticket.id}`;

  async function send(event: Event) {
    event.preventDefault();
    if (sending.current) return;
    const body = words.replace(/\r\n/g, "\n").trim();
    if (body === "") return setNews({ text: text.replyEmpty, ok: false });
    if ([...body].length > REPLY_MAX) return setNews({ text: text.replyTooLong, ok: false });
    sending.current = true;
    setBusy(true);
    setNews(null);
    try {
      await api.replyToTicket(csrf, ticket.id, body);
      setWords("");
      // The list is read again and the ticket usually leaves this view (it is answered now), so
      // the page, not this box, says it was sent.
      onSent();
    } catch (error) {
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") return onSignedOut();
      if (kind === "off") return onOff();
      setNews({
        text:
          kind === "no-ticket"
            ? text.ticketGone
            : kind === "bad-reply"
              ? text.replyTooLong
              : kind === "reply-off"
                ? text.replyOff
                : text.replyFailed,
        ok: false,
      });
    } finally {
      sending.current = false;
      setBusy(false);
      // The box and button were disabled while sending, which drops focus to the page: on a
      // failure put it back in the box (the words are still there). After a success the page's
      // "Reply sent." line takes focus (the ticket usually leaves this view).
      setTimeout(() => {
        if (document.activeElement === document.body && box.current?.value) box.current.focus();
      }, 0);
    }
  }

  return (
    <form onSubmit={send} noValidate>
      <fieldset disabled={busy}>
        <label for={id}>{text.replyLabel(ticket.subject)}</label>
        <textarea
          id={id}
          ref={box}
          rows={4}
          maxLength={REPLY_MAX}
          value={words}
          onInput={(e) => setWords((e.currentTarget as HTMLTextAreaElement).value)}
        />
        <p class="small muted">{text.replyHint}</p>
        <button type="submit" class="button">
          {busy ? text.replySending : text.replySend}
        </button>
      </fieldset>
      {news && (
        <p class={news.ok ? "ok" : "warn"} role={news.ok ? "status" : "alert"}>
          {news.text}
        </p>
      )}
    </form>
  );
}

export default function Tickets({ api, csrf, onSignedOut, onOff }: Props) {
  const [rows, setRows] = useState<SupportTicketRow[] | "loading" | "down">("loading");
  const [filter, setFilter] = useState<TicketFilter>("new");
  // Which page of the (filtered) list is showing, and how many there are (#2211). Pages stay the
  // same after a reply or a close, so the team keeps its place.
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState<number | null>(null);
  const [paging, setPaging] = useState(false);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const pageNow = useRef(1);
  // The filter showing, for a read that starts after an await (a close, a reply): the closure of
  // the render that pressed the button may be an older one.
  const filterNow = useRef<TicketFilter>("new");
  const [counts, setCounts] = useState<TicketCounts | null>(null);
  const [week, setWeek] = useState<TicketWeek | null>(null);
  // The ticket whose address was just copied (or failed to), so its row can say so.
  const [copied, setCopied] = useState<{ id: number; ok: boolean } | null>(null);
  const [news, setNews] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  // Read before re-rendering: two taps in the same moment mustn't send two requests.
  const inFlight = useRef(false);
  const newsBox = useRef<HTMLParagraphElement>(null);
  // The latest rows, for load() to read: its closure is from an older render.
  const rowsNow = useRef<typeof rows>("loading");
  rowsNow.current = rows;

  // Which read is the newest. Buttons can be pressed faster than the API answers, so an older,
  // slower answer must not replace a newer one (or show an error for a view no longer wanted).
  const newest = useRef(0);

  /** Reads one page of the list and resolves with the page that is showing afterwards. */
  async function load(
    show: TicketFilter = filterNow.current,
    wanted: number = pageNow.current,
    notice: string | null = null,
  ): Promise<number> {
    const mine = ++newest.current;
    setPaging(true);
    setPageNotice(notice);
    try {
      const found = await api.tickets(show, wanted);
      if (mine !== newest.current) return pageNow.current;
      setRows(found.tickets);
      setCounts(found.counts);
      setWeek(found.week ?? null);
      pageNow.current = found.page ?? 1;
      setPage(pageNow.current);
      setPages(found.pages ?? null);
      return pageNow.current;
    } catch (error) {
      if (mine !== newest.current) return pageNow.current;
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") onSignedOut();
      else if (kind === "off") onOff();
      // The page asked for is gone (the list got shorter: the last ticket on the last page was
      // closed): the page before it, and the first at the latest, is always there.
      else if (kind === "stale" && wanted > 1) {
        return load(show, wanted - 1, notice ?? text.pageGone);
      }
      // A list already on screen stays (the change was saved); only a first load shows the
      // error in its place.
      else if (Array.isArray(rowsNow.current)) {
        setNews({ text: text.ticketsDown, ok: false });
      } else setRows("down");
      return pageNow.current;
    } finally {
      if (mine === newest.current) setPaging(false);
    }
  }

  // Another view (or a first look) starts at page 1.
  useEffect(() => {
    pageNow.current = 1;
    filterNow.current = filter;
    void load(filter, 1);
  }, [api, filter]);

  // "Address copied." is true for a moment: it goes after a few seconds, so it can't be read as
  // "this is still on the clipboard" ten minutes later, and when another view is chosen.
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(null), 4000);
    return () => clearTimeout(timer);
  }, [copied]);

  // A "Done." takes focus: the list may be a screen away on a phone.
  useEffect(() => {
    if (news) newsBox.current?.focus();
  }, [news]);

  async function change(id: number, status: TicketStatus) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setNews(null);
    try {
      await api.setTicketStatus(csrf, id, status);
      setNews({ text: text.ticketDone(text.ticketStatuses[status] ?? status), ok: true });
      await load();
    } catch (error) {
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") {
        onSignedOut();
        return;
      }
      if (kind === "off") {
        onOff();
        return;
      }
      setNews({
        text:
          kind === "no-ticket"
            ? text.ticketGone
            : kind === "stale"
              ? text.ticketStale
              : text.ticketFailed,
        ok: false,
      });
      if (kind === "no-ticket") await load();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function copy(id: number, address: string) {
    try {
      await navigator.clipboard.writeText(address);
      setCopied({ id, ok: true });
    } catch {
      // No clipboard (an old browser, or the page isn't allowed): the address is still on screen.
      setCopied({ id, ok: false });
    }
  }

  return (
    <section aria-labelledby="tickets-heading" class="panel">
      <h2 id="tickets-heading">{text.ticketsHeading}</h2>
      <p>{text.ticketsLead}</p>
      {news && (
        <p class={news.ok ? "ok" : "warn"} role={news.ok ? "status" : "alert"} tabIndex={-1} ref={newsBox}>
          {news.text}
        </p>
      )}
      {week && <p>{weekLine(week)}</p>}
      <div class="row" role="group" aria-label={text.ticketsFilterLabel}>
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            class={f === filter ? "button" : "button secondary"}
            aria-pressed={f === filter}
            onClick={() => {
              if (f === filter) return;
              setNews(null);
              setCopied(null);
              setRows("loading");
              setPages(null);
              setFilter(f);
            }}
          >
            {counts
              ? text.ticketFilterCount(
                  text.ticketFilters[f] ?? f,
                  f === "all"
                    ? counts.new + counts.answered + counts.closed + counts.escalated
                    : counts[f],
                )
              : (text.ticketFilters[f] ?? f)}
          </button>
        ))}
      </div>
      {rows === "loading" && <p role="status">{text.listLoading}</p>}
      {rows === "down" && (
        <div class="stack">
          <p class="warn" role="alert">
            {text.ticketsDown}
          </p>
          <button type="button" class="button secondary" onClick={() => void load()}>
            {text.ticketsTryAgain}
          </button>
        </div>
      )}
      {Array.isArray(rows) && rows.length === 0 && <p>{text.ticketsNoneFor[filter] ?? text.ticketsNone}</p>}
      {Array.isArray(rows) && rows.length > 0 && (
        <ul class="tickets">
          {shown(rows, filter).map((t) => (
            <li key={t.id} data-ticket={t.id} data-status={t.status}>
              <h3>{t.subject}</h3>
              <p class="small muted">
                {text.ticketFrom(who(t.name, t.discordId), t.dmbotId, plan(t.plan), when(t.at))}
                {filter === "escalated" && ` · ${waitingFor(Math.floor(Date.now() / 1000) - waitingSince(t))}`}
              </p>
              <p class="small">
                {/* The account's address, for replying; it is not part of the ticket. */}
                {t.email ? (
                  <>
                    {text.replyTo}: <a href={`mailto:${encodeURIComponent(t.email)}`}>{t.email}</a>{" "}
                    <button
                      type="button"
                      class="button secondary"
                      aria-label={text.copyAddressFor(t.subject)}
                      onClick={() => void copy(t.id, t.email as string)}
                    >
                      {text.copyAddress}
                    </button>
                    {copied?.id === t.id && (
                      <span role="status" class={copied.ok ? "ok" : "warn"}>
                        {" "}
                        {copied.ok ? text.addressCopied : text.copyFailed}
                      </span>
                    )}
                  </>
                ) : (
                  text.noEmail
                )}
              </p>
              <p class="small">
                <strong>{text.ticketRowStatuses[t.status] ?? t.status}</strong>
              </p>
              {t.status === "escalated" && t.reason && text.escalationReasons[t.reason] && (
                <p class="small">
                  <strong>{text.escalationReasonLabel}:</strong> {text.escalationReasons[t.reason]}
                </p>
              )}
              <p class="ticket-text">{t.message}</p>
              <Thread replies={t.replies ?? []} />
              {t.status !== "closed" && (
                <ReplyBox
                  ticket={t}
                  api={api}
                  csrf={csrf}
                  onSent={() => {
                    setNews({ text: text.replySent, ok: true });
                    void load();
                  }}
                  onSignedOut={onSignedOut}
                  onOff={onOff}
                />
              )}
              <div class="row">
                {(t.status === "new" || t.status === "escalated") && (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    onClick={() => void change(t.id, "answered")}
                  >
                    {text.markAnswered}
                  </button>
                )}
                {t.status !== "closed" && (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    onClick={() => void change(t.id, "closed")}
                  >
                    {text.closeTicket}
                  </button>
                )}
                {t.status === "closed" && (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    onClick={() => void change(t.id, "new")}
                  >
                    {text.reopenTicket}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {pages !== null && (
        <PageBar
          id="tickets-page"
          label={text.ticketPagesNav}
          page={page}
          pages={pages}
          busy={paging}
          notice={pageNotice}
          onGo={(wanted) => load(filterNow.current, wanted)}
        />
      )}
    </section>
  );
}

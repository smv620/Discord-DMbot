/** @jsxImportSource preact */
// The admin page's support tickets panel (#1340): private messages from people on a paid plan,
// newest first, with Mark answered, Close and Reopen. The words are only ever shown here, to
// the signed-in admin; the API checks the session and the CSRF token on every change.
import { useEffect, useRef, useState } from "preact/hooks";

import { text, who } from "../content/admin";
import { planName } from "../content/account";
import { type PlanId } from "../content/pricing";
import {
  type AdminApi,
  AdminApiError,
  type SupportTicketRow,
  type TicketCounts,
  type TicketFilter,
  type TicketStatus,
} from "./api";

/** The filter buttons in the order the team needs them: what still needs a reply first. */
const FILTERS: TicketFilter[] = ["new", "answered", "closed", "all"];

interface Props {
  api: AdminApi;
  csrf: string;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
  /** The admin page was switched off while open: the page shows that. */
  onOff: () => void;
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

export default function Tickets({ api, csrf, onSignedOut, onOff }: Props) {
  const [rows, setRows] = useState<SupportTicketRow[] | "loading" | "down">("loading");
  const [filter, setFilter] = useState<TicketFilter>("new");
  const [counts, setCounts] = useState<TicketCounts | null>(null);
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

  async function load(show: TicketFilter = filter) {
    const mine = ++newest.current;
    try {
      const found = await api.tickets(show);
      if (mine !== newest.current) return;
      setRows(found.tickets);
      setCounts(found.counts);
    } catch (error) {
      if (mine !== newest.current) return;
      const kind = error instanceof AdminApiError ? error.kind : "down";
      if (kind === "signed-out") onSignedOut();
      else if (kind === "off") onOff();
      // A list already on screen stays (the change was saved); only a first load shows the
      // error in its place.
      else if (Array.isArray(rowsNow.current)) {
        setNews({ text: text.ticketsDown, ok: false });
      } else setRows("down");
    }
  }

  useEffect(() => {
    void load();
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
              setFilter(f);
            }}
          >
            {counts
              ? text.ticketFilterCount(
                  text.ticketFilters[f] ?? f,
                  f === "all" ? counts.new + counts.answered + counts.closed : counts[f],
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
          {rows.map((t) => (
            <li key={t.id} data-ticket={t.id} data-status={t.status}>
              <h3>{t.subject}</h3>
              <p class="small muted">
                {text.ticketFrom(who(t.name, t.discordId), t.dmbotId, plan(t.plan), when(t.at))}
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
              <p class="ticket-text">{t.message}</p>
              <div class="row">
                {t.status === "new" && (
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
    </section>
  );
}

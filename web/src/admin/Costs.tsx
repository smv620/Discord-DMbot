/** @jsxImportSource preact */
// The admin page's Costs panel (#3671): each paid service's charge for a month, read from the
// service itself where a read-only key allows it, with what it covers and a link to its own cost
// page. Every wait says what it is doing and for how long (CLAUDE.md, "Every wait shows…").
import { useEffect, useState } from "preact/hooks";

import { costsText as words, dollars } from "../content/admin";
import { type AdminApi, AdminApiError, type CostsView } from "./api";

interface Props {
  api: AdminApi;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
}

/** The last 12 months, newest first, as YYYY-MM. */
export function recentMonths(now: Date = new Date()): string[] {
  const out: string[] = [];
  for (let i = 0; i < 12; i++) {
    const d = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1),
    );
    out.push(d.toISOString().slice(0, 7));
  }
  return out;
}

const clock = (at: number): string =>
  new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

export default function Costs({ api, onSignedOut }: Props) {
  const months = recentMonths();
  const [month, setMonth] = useState(months[0] ?? "");
  const [view, setView] = useState<CostsView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [seconds, setSeconds] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setFailed(false);
    setSeconds(0);
    api.costs?.(month).then(
      (got) => {
        if (!live) return;
        setView(got);
        setBusy(false);
      },
      (error: unknown) => {
        if (!live) return;
        if (error instanceof AdminApiError && error.kind === "signed-out")
          return onSignedOut();
        setFailed(true);
        setBusy(false);
      },
    );
    return () => {
      live = false;
    };
  }, [api, month, tick]);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  if (!api.costs) return null;
  return (
    <section class="stack" aria-labelledby="costs-h">
      <h2 id="costs-h">{words.heading}</h2>
      <p>{words.lead}</p>
      <label for="costs-month">{words.month}</label>
      <select
        id="costs-month"
        value={month}
        onChange={(e) => setMonth(e.currentTarget.value)}
      >
        {months.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {busy && (
        <p role="status" aria-live="polite">
          {words.reading(seconds)}
        </p>
      )}
      {failed && (
        <div class="stack">
          <p class="warn" role="alert">
            {words.down}
          </p>
          <button
            type="button"
            class="button"
            onClick={() => setTick((n) => n + 1)}
          >
            {words.tryAgain}
          </button>
        </div>
      )}
      {view && !failed && view.month === month && (
        <div class="stack">
          <p>
            <strong>
              {view.counted.length > 0
                ? words.spent(dollars(view.spent_cents), view.counted.length, view.services.length)
                : words.noneRead}
            </strong>
          </p>
          <ul class="stack">
            {view.services.map((s) => (
              <li key={s.id}>
                <strong>{s.name}</strong>
                {s.amount_cents !== null && ` ${dollars(s.amount_cents)}`}
                <br />
                <span>{s.covers}</span>
                <br />
                <span>
                  {busy ? words.readingRow : s.detail}
                  {s.read_at !== null &&
                    !busy &&
                    ` · ${words.readAt(clock(s.read_at))}`}
                </span>
                <br />
                <a
                  href={s.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`${words.openPage}: ${s.name}`}
                >
                  {words.openPage}
                </a>
              </li>
            ))}
          </ul>
          <button
            type="button"
            class="button secondary"
            disabled={busy}
            onClick={() => setTick((n) => n + 1)}
          >
            {words.refresh}
          </button>
          <h3>{words.incomeHeading}</h3>
          <p>{words.incomeLead}</p>
          <table>
            <thead>
              <tr>
                <th scope="col">{words.plan}</th>
                <th scope="col">{words.income}</th>
                <th scope="col">{words.costs}</th>
              </tr>
            </thead>
            <tbody>
              {view.income.map((p) => (
                <tr key={p.plan}>
                  <th scope="row">{p.name}</th>
                  <td>{words.notAvailable}</td>
                  <td>{words.notAvailable}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

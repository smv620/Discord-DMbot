/** @jsxImportSource preact */
// "What DMbot recorded" on the admin Costs page (#3671): DMbot's own record of each paid call for the month, summed by service, plan
// and feature, shown beside the providers' figures and never instead of them. It is an estimate at list price: a service that is
// never priced (ElevenLabs, Voyage) shows its units and "not priced", never $0, and a sample run's test spend is shown apart and
// kept out of the plan and feature totals. No campaign, server or person is in the data. The wait says what it is doing and for how long.
import { useEffect, useState } from "preact/hooks";

import { microDollars, recordedText as words } from "../content/admin";
import { type AdminApi, AdminApiError, type RecordedRow, type RecordedView } from "./api";

interface Props {
  api: AdminApi;
  month: string;
  /** The admin session ended: the page goes back to the sign-in screen. */
  onSignedOut: () => void;
}

interface Sum {
  key: string;
  calls: number;
  units: number;
  micro: number;
  refused: number;
  unpriced: number;
}

/** The rows added up by one field, most expensive first (a stable order for equal costs: by name). */
export function sumBy(rows: RecordedRow[], field: "service" | "plan" | "feature"): Sum[] {
  const sums = new Map<string, Sum>();
  for (const r of rows) {
    const key = r[field];
    const s = sums.get(key) ?? { key, calls: 0, units: 0, micro: 0, refused: 0, unpriced: 0 };
    s.calls += r.calls;
    s.units += r.units_in + r.units_out;
    s.micro += r.micro_usd;
    s.refused += r.refused;
    s.unpriced += r.unpriced;
    sums.set(key, s);
  }
  return [...sums.values()].sort((a, b) => b.micro - a.micro || a.key.localeCompare(b.key));
}

/** A service is never priced when every call it made was recorded unpriced and cost nothing (ElevenLabs and Voyage). */
const neverPriced = (rows: RecordedRow[], service: string): boolean => {
  const own = rows.filter((r) => r.service === service && r.calls > 0);
  return own.length > 0 && own.every((r) => r.micro_usd === 0 && r.unpriced >= r.calls);
};

const num = (n: number): string => n.toLocaleString("en-US");

export default function Recorded({ api, month, onSignedOut }: Props) {
  const [view, setView] = useState<RecordedView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [seconds, setSeconds] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setFailed(false);
    setSeconds(0);
    api.costsRecorded?.(month).then(
      (got) => {
        if (!live) return;
        setView(got);
        setBusy(false);
      },
      (error: unknown) => {
        if (!live) return;
        if (error instanceof AdminApiError && error.kind === "signed-out") return onSignedOut();
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

  if (!api.costsRecorded) return null;
  const shown = view && !failed && view.month === month ? view : null;
  const play = shown ? shown.rows.filter((r) => r.source === "play") : [];
  const sample = shown ? shown.rows.filter((r) => r.source === "sample") : [];
  const byService = sumBy(play, "service");
  const unpriced = play.filter((r) => r.service !== "elevenlabs" && r.service !== "voyage").reduce((n, r) => n + r.unpriced, 0);
  const sampleMicro = sample.reduce((n, r) => n + r.micro_usd, 0);
  const sampleCalls = sample.reduce((n, r) => n + r.calls, 0);

  const table = (title: string, label: string, sums: Sum[], name: (key: string) => string, withUnits: boolean) => (
    <div class="stack">
      <h4>{title}</h4>
      <table>
        <thead>
          <tr>
            <th scope="col">{label}</th>
            <th scope="col">{words.calls}</th>
            {withUnits && <th scope="col">{words.used}</th>}
            <th scope="col">{words.cost}</th>
          </tr>
        </thead>
        <tbody>
          {sums.map((s) => (
            <tr key={s.key}>
              <th scope="row">
                {name(s.key)}
                {s.refused > 0 && <span class="small muted"> · {words.refused(s.refused)}</span>}
              </th>
              <td>{num(s.calls)}</td>
              {withUnits && (
                <td>
                  {num(s.units)} {words.units[s.key] ?? ""}
                </td>
              )}
              <td>{withUnits && neverPriced(play, s.key) ? words.notPriced : microDollars(s.micro)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <section class="stack" aria-labelledby="recorded-h">
      <h3 id="recorded-h">{words.heading}</h3>
      <p>{words.lead}</p>
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
          <button type="button" class="button" onClick={() => setTick((n) => n + 1)}>
            {words.tryAgain}
          </button>
        </div>
      )}
      {shown && shown.rows.length === 0 && <p>{words.none}</p>}
      {shown && shown.rows.length > 0 && (
        <div class="stack">
          {byService.length > 0 && table(words.byService, words.service, byService, (k) => k.charAt(0).toUpperCase() + k.slice(1), true)}
          {play.length > 0 && table(words.byPlan, words.plan, sumBy(play, "plan"), (k) => words.plans[k] ?? k, false)}
          {play.length > 0 && table(words.byFeature, words.feature, sumBy(play, "feature"), (k) => k, false)}
          {unpriced > 0 && <p class="small muted">{words.unpriced(unpriced)}</p>}
          {sample.length > 0 && (
            <div class="stack">
              <h4>{words.sampleHeading}</h4>
              <p class="small muted">{words.sampleLead}</p>
              <p>
                {num(sampleCalls)} {words.calls.toLowerCase()} · {microDollars(sampleMicro)}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

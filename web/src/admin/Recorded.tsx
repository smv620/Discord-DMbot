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
  /** Calls of a service that is never priced (ElevenLabs, Voyage): their cost is not zero, it is not known. */
  notPriced: number;
}

/** ElevenLabs and Voyage give no price to check against, so DMbot never prices them (#3671). One fixed list, not a guess from the rows. */
export const NEVER_PRICED = new Set(["elevenlabs", "voyage"]);

/** The rows added up by one field, most expensive first (a stable order for equal costs: by name). */
export function sumBy(rows: RecordedRow[], field: "service" | "plan" | "feature" | "source"): Sum[] {
  const sums = new Map<string, Sum>();
  for (const r of rows) {
    const key = r[field];
    const s = sums.get(key) ?? { key, calls: 0, units: 0, micro: 0, refused: 0, notPriced: 0 };
    s.calls += r.calls;
    s.units += r.units_in + r.units_out;
    s.micro += r.micro_usd;
    s.refused += r.refused;
    if (NEVER_PRICED.has(r.service)) s.notPriced += r.calls;
    sums.set(key, s);
  }
  return [...sums.values()].sort((a, b) => b.micro - a.micro || a.key.localeCompare(b.key));
}

/** A cost cell: "not priced" when every call was of a service that is never priced (never $0), "$1.20 + not priced" when some were. */
export function costCell(s: Sum): string {
  if (s.calls > 0 && s.notPriced >= s.calls) return words.notPriced;
  return s.notPriced > 0 ? words.plusNotPriced(microDollars(s.micro)) : microDollars(s.micro);
}

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
  const unpriced = play.filter((r) => !NEVER_PRICED.has(r.service)).reduce((n, r) => n + r.unpriced, 0);
  const sampleSum = sumBy(sample, "source")[0];

  const table = (id: string, title: string, label: string, sums: Sum[], name: (key: string) => string, withUnits: boolean) => (
    <div class="stack">
      <h4 id={id}>{title}</h4>
      <table aria-labelledby={id}>
        <thead>
          <tr>
            <th scope="col">{label}</th>
            <th scope="col">{words.calls}</th>
            {withUnits && <th scope="col">{words.used}</th>}
            <th scope="col">{words.cost}</th>
            <th scope="col">{words.refusedColumn}</th>
          </tr>
        </thead>
        <tbody>
          {sums.map((s) => (
            <tr key={s.key}>
              <th scope="row">{name(s.key)}</th>
              <td>{num(s.calls)}</td>
              {withUnits && (
                <td>
                  {num(s.units)} {words.units[s.key] ?? ""}
                </td>
              )}
              <td>{costCell(s)}</td>
              <td>{s.refused > 0 ? words.refused(s.refused) : ""}</td>
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
      <p role="status" aria-live="polite">
        {busy ? `${words.stage}. ${words.reading(seconds)}` : ""}
      </p>
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
          {byService.length > 0 && table("rec-service", words.byService, words.service, byService, (k) => words.services[k] ?? k, true)}
          {play.length > 0 && table("rec-plan", words.byPlan, words.plan, sumBy(play, "plan"), (k) => words.plans[k] ?? k, false)}
          {play.length > 0 && table("rec-feature", words.byFeature, words.feature, sumBy(play, "feature"), (k) => words.features[k] ?? k, false)}
          {unpriced > 0 && <p class="small muted">{words.unpriced(unpriced)}</p>}
          {sample.length > 0 && (
            <div class="stack">
              <h4>{words.sampleHeading}</h4>
              <p class="small muted">{words.sampleLead}</p>
              {sampleSum && (
                <p>
                  {num(sampleSum.calls)} {words.calls.toLowerCase()} · {costCell(sampleSum)}
                  {sampleSum.refused > 0 && ` · ${words.refused(sampleSum.refused)}`}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

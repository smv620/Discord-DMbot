/** @jsxImportSource preact */
// The website's "Game calendar" on a campaign's Settings (#5364 slice 3): a game master picks Days, Harptos, Four seasons or a calendar of
// their own, sees core's own preview line before saving, and the save is queued for the bot like the other settings. The page works out
// nothing about dates: the preview line, the current line and every problem are core's words, shown as they come. Four seasons is a
// template the page fills in and sends as "My own", so core needs no kind for it. Its own file, fetched only when a game master opens
// Settings.
import { useEffect, useRef, useState } from "preact/hooks";

import { calendarWords as words } from "../content/calendar-words";
import {
  type AccountApi,
  ApiError,
  type CalendarAnswer,
  type CalendarBody,
  type CalendarDayOne,
  type CalendarDef,
  type CalendarPart,
  type CalendarRead,
} from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;
const PREVIEW_MS = 400;

type Choice = "days" | "harptos" | "seasons" | "custom";
type Wait = { state: CalendarAnswer["state"] | "sending" | "timed_out"; since: number; progress?: CalendarAnswer["progress"] };

const seasons = (days: number): CalendarPart[] => words.seasonNames.map((name) => ({ name, days, kind: "season" as const }));
const blank = (): CalendarDef => ({ name: "", parts: [{ name: "", days: 30, kind: "month" }], leap: null, weekdays: [], yearLabel: "" });

export default function CalendarPanel({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [read, setRead] = useState<CalendarRead | null>(null);
  const [failed, setFailed] = useState(false);
  const [choice, setChoice] = useState<Choice>("harptos");
  const [def, setDef] = useState<CalendarDef>(blank());
  const [seasonDays, setSeasonDays] = useState(91);
  const [dayOne, setDayOne] = useState<CalendarDayOne>({ year: 1, part: "", day: 1, weekday: null });
  const [preview, setPreview] = useState<{ line?: string; problems?: string[] } | "checking" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const live = useRef(true);
  const looks = useRef(0);
  useEffect(() => () => void (live.current = false), []);

  async function load(): Promise<void> {
    if (!api.calendar) return;
    try {
      const got = await api.calendar(campaignRef);
      if (!live.current) return;
      setRead(got);
      setFailed(false);
      setChoice(got.kind === "custom" ? "custom" : got.kind);
      if (got.def) setDef(got.def);
      if (got.dayOne) setDayOne(got.dayOne);
    } catch (error) {
      // Not a game master (or not there): the panel is simply not shown.
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setRead(null);
      else if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    setRead(null);
    setWait(null);
    setProblems([]);
    void load();
  }, [api, campaignRef]);

  // What is sent for the choice: Four seasons is "My own" with its four parts, Days sends no definition.
  const sentDef = (): CalendarDef => (choice === "seasons" ? { name: words.kinds.seasons, parts: seasons(seasonDays), leap: null, weekdays: [], yearLabel: "" } : def);
  const body = (): CalendarBody =>
    choice === "days" ? { kind: "days" } : { kind: choice === "harptos" ? "harptos" : "custom", ...(choice === "harptos" ? {} : { def: sentDef() }), dayOne };
  const partNames = (choice === "seasons" ? seasons(seasonDays) : def.parts).map((p) => p.name);
  // Day 1's part follows the parts: a removed or renamed one falls back to the first.
  useEffect(() => {
    if (choice === "harptos" || choice === "days") return;
    if (!partNames.includes(dayOne.part) && partNames[0] !== undefined) setDayOne((one) => ({ ...one, part: partNames[0] ?? "" }));
  }, [partNames.join("\u0000"), choice]);

  // The preview, asked of core as the fields change (a pause first, so typing is not a request a letter).
  useEffect(() => {
    if (!read || !api.calendarPreview || choice === "days") return setPreview(null);
    const mine = ++looks.current;
    setPreview("checking");
    const timer = setTimeout(() => {
      api
        .calendarPreview?.(campaignRef, body())
        .then((got) => {
          if (live.current && mine === looks.current) setPreview(got);
        })
        .catch(() => {
          if (live.current && mine === looks.current) setPreview(null);
        });
    }, PREVIEW_MS);
    return () => clearTimeout(timer);
  }, [read !== null, choice, JSON.stringify(def), seasonDays, JSON.stringify(dayOne)]);

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";

  async function save(): Promise<void> {
    if (!api.saveCalendar || !api.calendarAnswer || busy) return;
    setConfirming(false);
    setProblems([]);
    const since = Math.floor(Date.now() / 1000);
    setWait({ state: "sending", since });
    let queued = false;
    try {
      const id = await api.saveCalendar(campaignRef, body());
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.calendarAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ state: got.state, since, progress: got.progress });
        if (got.state === "done") return void (await load());
        if (got.state === "failed") {
          setProblems(got.error === "bad_calendar" && got.problems.length > 0 ? got.problems : [got.error === "no_clock" ? words.noClockError : got.error === "not_allowed" ? words.notAllowed : words.failed]);
          return;
        }
      }
      if (live.current) setWait({ state: "timed_out", since });
    } catch {
      if (queued) return void setWait({ state: "timed_out", since }); // sent: only the look failed, so it may well have worked
      setWait(null);
      setProblems([words.failed]);
    }
  }
  // Leaving a calendar of the DM's own for Days or Harptos removes it: said and confirmed once first (the Expert's point 2).
  const removing = read?.kind === "custom" && (choice === "days" || choice === "harptos");
  const press = (): void => (removing && !confirming ? setConfirming(true) : void save());

  if (failed && !read)
    return (
      <div class="stack">
        <h3>{words.heading}</h3>
        <p role="alert" class="warn">{words.loadFailed}</p>
        <button type="button" class="button secondary" onClick={() => void load()}>{words.reload}</button>
      </div>
    );
  if (!read) return null;
  const ok = preview !== null && preview !== "checking" && preview.problems === undefined;
  const setPart = (i: number, patch: Partial<CalendarPart>): void => setDef({ ...def, parts: def.parts.map((p, n) => (n === i ? { ...p, ...patch } : p)) });
  return (
    <div class="stack">
      <h3>{words.heading}</h3>
      <p>{words.lead}</p>
      {read.line && (
        <p>
          <strong>{words.nowLabel}:</strong> {read.line}
        </p>
      )}
      <fieldset class="stack">
        <legend class="visually-hidden">{words.heading}</legend>
        {(["days", "harptos", "seasons", "custom"] as const).map((k) => (
          <label class="choice" key={k}>
            <input type="radio" name={`calendar-${campaignRef}`} checked={choice === k} disabled={busy} onChange={() => (setChoice(k), setConfirming(false))} />
            <span>{words.kinds[k]}</span>
          </label>
        ))}
      </fieldset>
      {choice === "seasons" && (
        <label>
          {words.seasonsDays} <input type="number" min={1} max={250} value={seasonDays} onInput={(e) => setSeasonDays(Number((e.currentTarget as HTMLInputElement).value))} />
        </label>
      )}
      {choice === "custom" && (
        <div class="stack">
          <label>
            {words.name} <input type="text" maxLength={30} value={def.name} onInput={(e) => setDef({ ...def, name: (e.currentTarget as HTMLInputElement).value })} />
          </label>
          <h4>{words.partsHeading}</h4>
          {def.parts.map((p, i) => (
            <div class="row" key={i}>
              <label>
                {words.partName} <input type="text" maxLength={30} value={p.name} onInput={(e) => setPart(i, { name: (e.currentTarget as HTMLInputElement).value })} />
              </label>
              <label>
                {words.partDays} <input type="number" min={1} value={p.days} onInput={(e) => setPart(i, { days: Number((e.currentTarget as HTMLInputElement).value) })} />
              </label>
              <label>
                {words.partKind}{" "}
                <select value={p.kind} onChange={(e) => setPart(i, { kind: (e.currentTarget as HTMLSelectElement).value as CalendarPart["kind"] })}>
                  {(["month", "season", "festival"] as const).map((k) => (
                    <option key={k} value={k}>{words.partKinds[k]}</option>
                  ))}
                </select>
              </label>
              <button type="button" class="button secondary" disabled={def.parts.length <= 1} onClick={() => setDef({ ...def, parts: def.parts.filter((_, n) => n !== i) })}>
                {words.removePart(p.name)}
              </button>
            </div>
          ))}
          <p>
            <button type="button" class="button secondary" disabled={def.parts.length >= 40} onClick={() => setDef({ ...def, parts: [...def.parts, { name: "", days: 30, kind: "month" }] })}>
              {words.addPart}
            </button>
          </p>
          <label class="choice">
            <input type="checkbox" checked={def.leap !== null} onChange={(e) => setDef({ ...def, leap: (e.currentTarget as HTMLInputElement).checked ? { every: 4, name: "", after: def.parts[0]?.name ?? "" } : null })} />
            <span>{words.leapOn}</span>
          </label>
          {def.leap && (
            <div class="row">
              <label>
                {words.leapEvery} <input type="number" min={2} value={def.leap.every} onInput={(e) => setDef({ ...def, leap: { ...(def.leap as NonNullable<CalendarDef["leap"]>), every: Number((e.currentTarget as HTMLInputElement).value) } })} />
              </label>
              <label>
                {words.leapName} <input type="text" maxLength={30} value={def.leap.name} onInput={(e) => setDef({ ...def, leap: { ...(def.leap as NonNullable<CalendarDef["leap"]>), name: (e.currentTarget as HTMLInputElement).value } })} />
              </label>
              <label>
                {words.leapAfter}{" "}
                <select value={def.leap.after} onChange={(e) => setDef({ ...def, leap: { ...(def.leap as NonNullable<CalendarDef["leap"]>), after: (e.currentTarget as HTMLSelectElement).value } })}>
                  {def.parts.map((p, i) => (
                    <option key={i} value={p.name}>{p.name}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <label>
            {words.weekdays}{" "}
            <input type="text" value={def.weekdays.join(", ")} onInput={(e) => setDef({ ...def, weekdays: (e.currentTarget as HTMLInputElement).value.split(",").map((w) => w.trim()).filter((w) => w !== "") })} />
          </label>
          <label>
            {words.yearLabel} <input type="text" maxLength={8} value={def.yearLabel} onInput={(e) => setDef({ ...def, yearLabel: (e.currentTarget as HTMLInputElement).value })} />
          </label>
        </div>
      )}
      {choice !== "days" && (
        <fieldset class="stack">
          <legend>{words.dayOneHeading}</legend>
          <div class="row">
            <label>
              {words.dayOneYear} <input type="number" min={1} value={dayOne.year} onInput={(e) => setDayOne({ ...dayOne, year: Number((e.currentTarget as HTMLInputElement).value) })} />
            </label>
            <label>
              {words.dayOnePart}{" "}
              {choice === "harptos" ? (
                <input type="text" value={dayOne.part} onInput={(e) => setDayOne({ ...dayOne, part: (e.currentTarget as HTMLInputElement).value })} />
              ) : (
                <select value={dayOne.part} onChange={(e) => setDayOne({ ...dayOne, part: (e.currentTarget as HTMLSelectElement).value })}>
                  {partNames.map((n, i) => (
                    <option key={i} value={n}>{n}</option>
                  ))}
                </select>
              )}
            </label>
            <label>
              {words.dayOneDay} <input type="number" min={1} value={dayOne.day ?? 1} onInput={(e) => setDayOne({ ...dayOne, day: Number((e.currentTarget as HTMLInputElement).value) })} />
            </label>
            {choice === "custom" && def.weekdays.length > 0 && (
              <label>
                {words.dayOneWeekday}{" "}
                <select value={dayOne.weekday ?? def.weekdays[0]} onChange={(e) => setDayOne({ ...dayOne, weekday: (e.currentTarget as HTMLSelectElement).value })}>
                  {def.weekdays.map((d, i) => (
                    <option key={i} value={d}>{d}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </fieldset>
      )}
      {preview === "checking" && <p class="small muted" role="status">{words.previewChecking}</p>}
      {preview !== null && preview !== "checking" && preview.line && (
        <p role="status">
          <strong>{words.previewHeading}:</strong> {preview.line}
        </p>
      )}
      {preview !== null && preview !== "checking" && preview.problems && (
        <ul role="alert" class="warn">
          {preview.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {!read.hasClock && <p class="small muted">{words.noClock}</p>}
      {confirming && (
        <p role="alert" class="warn">
          {words.removeWarning}
        </p>
      )}
      <div class="row">
        <button type="button" class="button" disabled={busy || !read.hasClock || (choice !== "days" && !ok)} onClick={press}>
          {confirming ? words.removeSwitch : words.save}
        </button>
        {confirming && (
          <button type="button" class="button secondary" onClick={() => (setConfirming(false), setChoice("custom"))}>
            {words.removeKeep}
          </button>
        )}
      </div>
      {problems.length > 0 && (
        <ul role="alert" class="warn">
          {problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {wait?.state === "done" && <p role="status">{words.saved}</p>}
      {wait?.state === "timed_out" && <p role="status">{words.quiet}</p>}
      {wait && wait.state !== "done" && wait.state !== "timed_out" && wait.state !== "failed" && (
        <WorkProgress
          stage={wait.state === "working" ? words.working : words.waiting}
          startedAt={wait.progress?.startedAt ?? wait.since}
          updatedAt={wait.progress?.updatedAt}
          serverNow={wait.progress?.serverNow}
          state={wait.progress?.state ?? "running"}
        />
      )}
      <p class="small muted">{words.stays}</p>
    </div>
  );
}

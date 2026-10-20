/** @jsxImportSource preact */
// The Import sessions page (#2107; docs/PLAN.md, "Importing older sessions"): a game master picks
// text files, says which server member each name in them is, sets dates and order, checks what
// would be kept, and starts the import. Consent is the API's: lines of someone who has not agreed
// are dropped whatever is picked here, and the page says so; it never decides who agreed. The files
// travel as text and are sent again at each step because the API keeps nothing between calls.
// Shown only while IMPORT_LIVE is true.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import {
  IMPORT_MAX_FILES,
  IMPORT_MAX_MB,
  IMPORT_LIVE,
  importPage as words,
} from "../content/import-words";
import {
  ApiError,
  type AccountApi,
  type ImportCheck,
  type ImportFileIn,
  type ImportMember,
  type ImportPreview,
  type ImportProgress,
} from "./api";

/** The campaign's address, read from `/account/import/<server>.<campaign>`. */
export function campaignFromPath(path: string): string | null {
  const match = /^\/account\/import\/([^/]+)\/?$/.exec(path);
  return match ? decodeURIComponent(match[1] ?? "") : null;
}

const DROP = "drop";
const POLL_MS = 3000;
const isDate = (s: string): boolean => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s));

interface Folded {
  label: string;
  lines: number;
  suggested: string | null;
  why: string | null;
  suggestedName: string | null;
}

export default function Import({ api, path }: { api: AccountApi; path: string }) {
  const ref = campaignFromPath(path);
  const [picked, setPicked] = useState<File[]>([]);
  const [texts, setTexts] = useState<ImportFileIn[]>([]);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [known, setKnown] = useState<Record<string, ImportMember>>({});
  const [query, setQuery] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [check, setCheck] = useState<ImportCheck | null>(null);
  const [dates, setDates] = useState<Record<string, string>>({});
  const [guessed, setGuessed] = useState<Record<string, boolean>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [importId, setImportId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const inFlight = useRef(false);
  const problemRef = useRef<HTMLParagraphElement>(null);

  const fail = (error: unknown): void => {
    const kind = error instanceof ApiError ? error.kind : "server";
    if (kind === "signed-out") setSignedOut(true);
    setProblem(words.problem[kind] ?? words.problem.server ?? "");
    // The message is announced by its role; focus moves so a keyboard user lands on it.
    setTimeout(() => problemRef.current?.focus(), 0);
  };

  /** Runs one request at a time, with the busy state and the problem line every step shares. */
  const run = async (work: () => Promise<void>): Promise<void> => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await work();
    } catch (error) {
      fail(error);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const usable = useMemo(
    () => (preview?.files ?? []).filter((f) => f.error === null),
    [preview],
  );

  // Fold the same name across files into one line to answer.
  const folded: Folded[] = useMemo(() => {
    const seen = new Map<string, Folded>();
    for (const file of usable) {
      for (const s of file.speakers) {
        const have = seen.get(s.label);
        if (have) have.lines += s.lines;
        else
          seen.set(s.label, {
            label: s.label,
            lines: s.lines,
            suggested: s.suggested?.userId ?? null,
            why: s.suggested?.why ?? null,
            suggestedName: s.suggested?.name ?? null,
          });
      }
    }
    return [...seen.values()];
  }, [usable]);

  // Follow the work once it has started, until every session is done or failed.
  useEffect(() => {
    if (!importId || !ref || !api.importProgress) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (): Promise<void> => {
      try {
        const next = await api.importProgress!(ref, importId);
        if (!live) return;
        setProgress(next);
        if (next.sessions.some((s) => s.state === "waiting" || s.state === "working"))
          timer = setTimeout(() => void tick(), POLL_MS);
      } catch (error) {
        if (live) fail(error);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref, importId]);

  if (!IMPORT_LIVE || !api.importPreview || !ref) return null;

  const remember = (list: ImportMember[]): void =>
    setKnown((now) => {
      const next = { ...now };
      for (const m of list) next[m.userId] = m;
      return next;
    });

  const choose = (event: Event): void => {
    const list = Array.from((event.target as HTMLInputElement).files ?? []);
    setPicked(list);
    setProblem(null);
    if (list.length > IMPORT_MAX_FILES) setProblem(words.tooManyFiles);
    else {
      const big = list.find((f) => f.size > IMPORT_MAX_MB * 1024 * 1024);
      const odd = list.find((f) => !f.name.toLowerCase().endsWith(".txt"));
      if (big) setProblem(words.fileTooBig(big.name));
      else if (odd) setProblem(words.fileNotTxt(odd.name));
    }
  };

  const lookAtFiles = (): Promise<void> =>
    run(async () => {
      const files: ImportFileIn[] = await Promise.all(
        picked.map(async (f) => ({ name: f.name, text: await f.text() })),
      );
      const found = await api.importPreview!(ref, files);
      const good = found.files.filter((f) => f.error === null);
      setTexts(files);
      setPreview(found);
      setCheck(null);
      const start: Record<string, string> = {};
      for (const f of good)
        for (const s of f.speakers) start[s.label] ??= s.suggested?.userId ?? DROP;
      setMapping(start);
      setDates(Object.fromEntries(good.map((f) => [f.name, f.suggestedDate ?? ""])));
      setGuessed(Object.fromEntries(good.map((f) => [f.name, f.suggestedDate !== null])));
      setOrder(good.map((f) => f.name));
      const suggestedPeople: ImportMember[] = good.flatMap((f) =>
        f.speakers.flatMap((s) =>
          s.suggested ? [{ userId: s.suggested.userId, name: s.suggested.name, agreed: true }] : [],
        ),
      );
      remember(suggestedPeople);
      if (api.importMembers) remember(await api.importMembers(ref, ""));
    });

  const search = (value: string): void => {
    setQuery(value);
    if (!api.importMembers) return;
    void api.importMembers(ref, value).then(remember, fail);
  };

  const sentFiles = (): ImportFileIn[] => texts.filter((t) => order.includes(t.name));

  const doCheck = (): Promise<void> =>
    run(async () => {
      if (!api.importCheck) return;
      setCheck(await api.importCheck(ref, sentFiles(), mapping));
    });

  const move = (name: string, by: -1 | 1): void =>
    setOrder((now) => {
      const i = now.indexOf(name);
      const j = i + by;
      if (i < 0 || j < 0 || j >= now.length) return now;
      const next = [...now];
      [next[i], next[j]] = [next[j] as string, next[i] as string];
      return next;
    });

  const allDates = order.every((n) => isDate(dates[n] ?? ""));

  const start = (): Promise<void> =>
    run(async () => {
      if (!api.importStart) return;
      const started = await api.importStart(ref, sentFiles(), {
        mapping,
        sessions: order.map((file, i) => ({ file, date: dates[file] ?? "", order: i + 1 })),
      });
      setProgress({
        sessions: started.sessions.map((s) => ({ file: s.file, date: dates[s.file] ?? "", state: s.state, reason: null })),
      });
      setImportId(started.importId);
    });

  const memberOptions = Object.values(known);
  const label = (m: ImportMember): string => (m.agreed ? m.name : `${m.name} (${words.notAgreedMark})`);

  if (signedOut)
    return (
      <div class="panel" role="status">
        <p>{words.signedOut}</p>
        <a class="button" href={api.signInUrl()}>
          {words.signIn}
        </a>
      </div>
    );

  // After the import has started, only the progress is shown.
  if (progress) {
    const done = progress.sessions.every((s) => s.state === "done" || s.state === "failed");
    const failed = progress.sessions.some((s) => s.state === "failed");
    return (
      <div class="stack">
        <h2>{words.progressHeading}</h2>
        {problem && (
          <p class="note bad" role="alert" tabIndex={-1} ref={problemRef}>
            {problem}
          </p>
        )}
        <ul class="campaigns">
          {progress.sessions.map((s) => (
            <li key={s.file}>
              <span class="campaign-name">{s.file}</span>
              <span class="small">
                {s.date} · {words.state[s.state] ?? s.state}
                {s.state === "failed" && s.reason
                  ? ` · ${words.failedReason[s.reason] ?? ""}`
                  : ""}
              </span>
            </li>
          ))}
        </ul>
        {done && (
          <p role="status">
            {failed ? words.someFailed : words.allDone}{" "}
            <a href={`/account/transcripts/${encodeURIComponent(ref)}`}>{words.seeTranscripts}</a>
          </p>
        )}
      </div>
    );
  }

  return (
    <div class="stack">
      <p class="muted small">{words.lead}</p>
      {problem && (
        <p class="note bad" role="alert" tabIndex={-1} ref={problemRef}>
          {problem}
        </p>
      )}

      <section class="panel" aria-labelledby="import-pick">
        <h2 id="import-pick">{words.pickLabel}</h2>
        <input type="file" accept=".txt,text/plain" multiple onChange={choose} aria-label={words.pickLabel} />
        {picked.length > 0 && <p class="small">{words.chosen(picked.length)}</p>}
        <button
          type="button"
          class="button"
          disabled={busy || picked.length === 0 || picked.length > IMPORT_MAX_FILES || problem !== null}
          onClick={() => void lookAtFiles()}
        >
          {busy && !preview ? words.working : words.preview}
        </button>
      </section>

      {preview && (
        <>
          <ul class="campaigns">
            {preview.files
              .filter((f) => f.error !== null)
              .map((f) => (
                <li key={f.name}>
                  <span class="campaign-name">{f.name}</span>
                  <span class="small">{words.fileError[f.error ?? ""] ?? words.fileErrorOther}</span>
                </li>
              ))}
          </ul>
          {usable.length === 0 ? (
            <p class="note bad" role="alert">
              {words.noUsableFiles}
            </p>
          ) : (
            <>
              <section class="panel" aria-labelledby="import-match">
                <h2 id="import-match">{words.matchHeading}</h2>
                <p class="small">{words.matchLead}</p>
                <label class="field">
                  {words.find}
                  <input
                    type="search"
                    value={query}
                    onInput={(e) => search((e.target as HTMLInputElement).value)}
                    aria-describedby="import-find-hint"
                  />
                </label>
                <p class="muted small" id="import-find-hint">
                  {words.findHint}
                </p>
                <ul class="campaigns">
                  {folded.map((s) => {
                    const chosen = mapping[s.label] ?? DROP;
                    const who = known[chosen];
                    return (
                      <li key={s.label}>
                        <span class="campaign-name">{s.label}</span>
                        <span class="small">
                          {words.lines(s.lines)}
                          {s.why && s.suggested === chosen ? ` · ${words.suggestedBecause(s.why)}` : ""}
                        </span>
                        <select
                          aria-label={`${s.label}: ${words.matchHeading}`}
                          value={chosen}
                          onChange={(e) => {
                            const value = (e.target as HTMLSelectElement).value;
                            setCheck(null);
                            setMapping((now) => ({ ...now, [s.label]: value }));
                          }}
                        >
                          <option value={DROP}>{words.leaveOut}</option>
                          {memberOptions.map((m) => (
                            <option key={m.userId} value={m.userId}>
                              {label(m)}
                            </option>
                          ))}
                        </select>
                        {who && !who.agreed && (
                          <span class="small" role="note">
                            {words.droppedReason.not_agreed}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <button type="button" class="button secondary" disabled={busy} onClick={() => void doCheck()}>
                  {words.checkButton}
                </button>
                {check && (
                  <div role="status">
                    <p class="small">{words.checkLead}</p>
                    <ul class="campaigns">
                      {check.files.map((f) => (
                        <li key={f.name}>
                          <span class="campaign-name">{f.name}</span>
                          {f.leftOut > 0 && <span class="small">{words.leftOutFile(f.leftOut)}</span>}
                          {f.speakers.map((s) => (
                            <span class="small" key={s.label}>
                              {s.label}: {words.kept(s.kept)}
                              {Object.entries(s.dropped)
                                .filter(([, n]) => n > 0)
                                .map(([why, n]) => `; ${words.dropped(n, words.droppedReason[why] ?? why)}`)
                                .join("")}
                            </span>
                          ))}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>

              <section class="panel" aria-labelledby="import-dates">
                <h2 id="import-dates">{words.datesHeading}</h2>
                <p class="small">{words.datesLead}</p>
                <ol class="campaigns">
                  {order.map((name, i) => (
                    <li key={name}>
                      <span class="campaign-name">{name}</span>
                      <span class="small">{words.position(i + 1, order.length)}</span>
                      <label class="field">
                        {words.dateLabel(name)}
                        <input
                          type="date"
                          value={dates[name] ?? ""}
                          onInput={(e) => {
                            const value = (e.target as HTMLInputElement).value;
                            setDates((now) => ({ ...now, [name]: value }));
                            setGuessed((now) => ({ ...now, [name]: false }));
                          }}
                        />
                      </label>
                      {guessed[name] && <span class="small">{words.dateGuess}</span>}
                      <span class="row">
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={words.moveUp(name)}
                          disabled={i === 0}
                          onClick={() => move(name, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={words.moveDown(name)}
                          disabled={i === order.length - 1}
                          onClick={() => move(name, 1)}
                        >
                          ↓
                        </button>
                      </span>
                    </li>
                  ))}
                </ol>
              </section>

              <section class="panel" aria-labelledby="import-confirm">
                <h2 id="import-confirm">{words.confirmHeading}</h2>
                <p>{words.estimate(order.length, preview.estimate.fastCalls)}</p>
                <p class="small">{words.confirmNote}</p>
                <button type="button" class="button" disabled={busy || !allDates || order.length === 0} onClick={() => void start()}>
                  {busy ? words.working : words.importButton}
                </button>
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}

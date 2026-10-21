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
import { RECAPS_LIVE } from "../content/recap-words";
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
/** While every unfinished session is only waiting for the DM's answers about names, nothing on the
 * page changes until they answer, so ask once a minute instead (#2230). */
const SLOW_POLL_MS = 60_000;

type Session = ImportProgress["sessions"][number];

/** A session that will not change on its own any more. A session waiting for names is not final:
 * the DM's answers move it on. Without a stage (an older API) the plain state decides. */
function isFinal(s: Session): boolean {
  // A stage this page doesn't know (a newer API) is read by the plain state, never polled forever.
  if (!s.stage || !(Object.hasOwn(words.stage, s.stage) || s.stage === "waiting_for_names"))
    return s.state === "done" || s.state === "failed";
  return (
    s.stage === "recap_ready" ||
    s.stage === "recap_failed" ||
    s.stage === "recap_not_written" ||
    s.stage === "done" ||
    s.stage === "failed"
  );
}

function isFailed(s: Session): boolean {
  return s.stage ? s.stage === "failed" : s.state === "failed";
}

/** Why recaps can't be written for this campaign, when it is something the DM can change: the
 * reason's own line, with the plans link for a plan. Nothing for any other reason (#2229). */
function RecapsWhyLine({ why }: { why: string }) {
  const line = words.recapsWhy[why];
  if (!line) return null;
  return (
    <>
      {line}
      {why === "plan" && (
        <>
          {" "}
          <a href="/pricing">{words.recapsPlans}</a>
        </>
      )}
    </>
  );
}

/** Where a session's recap link goes: its own recap when the API names it (#2240), else the list. */
function recapHref(s: Session): string {
  return s.recap ? `/account/recaps/${encodeURIComponent(s.recap)}` : "/account/recaps";
}

/** The words for where one session has got to; the plain state's words when there is no stage. */
function stageLine(s: Session): string {
  if (!s.stage) return words.state[s.state] ?? s.state;
  if (s.stage === "waiting_for_names") return words.waitingForNames(s.namesToAnswer ?? 0);
  return words.stage[s.stage] ?? words.state[s.state] ?? s.state;
}
/** A real calendar day: the round trip catches "2026-02-31", which some engines would shift. */
const isDate = (s: string): boolean =>
  /^\d{4}-\d{2}-\d{2}$/.test(s) &&
  new Date(`${s}T00:00:00Z`).toISOString().slice(0, 10) === s;

interface Folded {
  label: string;
  lines: number;
  suggested: string | null;
  why: string | null;
}

/** What to add after a session's state: its reason, or the plain fallback for a failure with no
 * known reason. Nothing for any other state without one, so a line never ends in a bare " · ". */
function reasonLine(state: string, reason: string | null): string {
  const known = reason ? words.failedReason[reason] : undefined;
  return known ?? (state === "failed" ? words.failedFallback : "");
}

export default function Import({
  api,
  path,
}: {
  api: AccountApi;
  path: string;
}) {
  const ref = campaignFromPath(path);
  const [picked, setPicked] = useState<File[]>([]);
  // Google Doc links (#2140) sit in the same list as the files, 20 items in all.
  const [links, setLinks] = useState<string[]>([]);
  const [linkText, setLinkText] = useState("");
  const [linkProblem, setLinkProblem] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const linkBox = useRef<HTMLInputElement>(null);
  // The links that opened, in the order they are sent from here on, and what each one is called
  // there: the API names a link by its place in the list it is sent, so dropping a bad link renames the rest.
  const [sentLinks, setSentLinks] = useState<string[]>([]);
  const [sentNames, setSentNames] = useState<Record<string, string>>({});
  const [texts, setTexts] = useState<ImportFileIn[]>([]);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [known, setKnown] = useState<Record<string, ImportMember>>({});
  const [query, setQuery] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [check, setCheck] = useState<ImportCheck | null>(null);
  const [dates, setDates] = useState<Record<string, string>>({});
  const [guessed, setGuessed] = useState<Record<string, boolean>>({});
  const [order, setOrder] = useState<string[]>([]);
  // The sessions the DM wants a recap of (by file name): none until ticked (#2221).
  const [recaps, setRecaps] = useState<Record<string, boolean>>({});
  const [importId, setImportId] = useState<string | null>(null);
  // Set when the start reply says recaps weren't possible although ticks were sent (#2229).
  const [startedWithoutRecaps, setStartedWithoutRecaps] = useState<string | null>(null);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // A problem with the chosen files themselves: it blocks Preview until other files are chosen.
  const [pickProblem, setPickProblem] = useState<string | null>(null);
  const [moved, setMoved] = useState("");
  const [signedOut, setSignedOut] = useState(false);
  const inFlight = useRef(false);
  const searchSeq = useRef(0);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [pollTries, setPollTries] = useState(0);

  const fail = (error: unknown): void => {
    const kind = error instanceof ApiError ? error.kind : "server";
    if (kind === "signed-out") setSignedOut(true);
    // The message is announced by its role="alert"; moving focus too would read it twice.
    // A doc can stop opening after Preview (sharing turned off), so "the files" is the wrong advice.
    const key = kind === "bad-files" && sentLinks.length > 0 ? "bad-files-docs" : kind;
    setProblem(words.problem[key] ?? words.problem.server ?? "");
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
          });
      }
    }
    return [...seen.values()];
  }, [usable]);

  // While the site's switch is off, ask whether this campaign's server has imports on (#2260). The
  // page's address is `<server>.<campaign>`; /me lists the campaign by its own id.
  const [open, setOpen] = useState<"unknown" | "on" | "off">(IMPORT_LIVE ? "on" : "unknown");
  useEffect(() => {
    if (IMPORT_LIVE || !ref) return;
    let live = true;
    setOpen("unknown");
    const campaign = ref.split(".").slice(1).join(".");
    api
      .me()
      .then((me) => {
        if (!live) return;
        if (me === null) {
          // Not signed in: the page's own sign-in panel says so.
          setSignedOut(true);
          setOpen("on");
          return;
        }
        setOpen(me.campaigns.some((c) => c.id === campaign && c.importsOn === true) ? "on" : "off");
      })
      .catch(() => live && setOpen("off"));
    return () => {
      live = false;
    };
  }, [api, ref]);

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
        const open = next.sessions.filter((s) => !isFinal(s));
        if (open.length > 0) {
          const onlyNames = open.every((s) => s.stage === "waiting_for_names");
          timer = setTimeout(() => void tick(), onlyNames ? SLOW_POLL_MS : POLL_MS);
        }
      } catch (error) {
        if (live) fail(error);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // `fail` only sets state, so it is left out of the dependencies on purpose; pollTries restarts it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref, importId, pollTries]);

  // Nothing is shown unless the API has the routes (an older API has none) and importing is open:
  // for the whole site (IMPORT_LIVE), or for this campaign's server alone (`importsOn`, #2260).
  if (!api.importPreview || !ref) return null;
  if (!IMPORT_LIVE && open === "unknown")
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (!IMPORT_LIVE && open === "off") return <p>{words.notOpen}</p>;

  const remember = (list: ImportMember[]): void =>
    setKnown((now) => {
      const next = { ...now };
      // A later answer from the members list ("agreed" known) wins over a bare suggestion.
      for (const m of list) next[m.userId] = { ...next[m.userId], ...m };
      return next;
    });

  /** The name the DM saw in the list they built, for a name the API sent back (it renames links by place). */
  const shownApi = (apiName: string): string => {
    const seen = Object.entries(sentNames).find(([, sent]) => sent === apiName)?.[0];
    return words.shownName(seen ?? apiName);
  };

  /** Everything after Preview comes from one snapshot of the files and links, so a change clears it all. */
  const forgetPreview = (): void => {
    setProblem(null);
    setPreview(null);
    setTexts([]);
    setCheck(null);
    setOrder([]);
    setMapping({});
    setDates({});
    setGuessed({});
    setKnown({});
    setSentLinks([]);
    setSentNames({});
    // Ticks belong to the files they were made for; new files start unticked (#2229).
    setRecaps({});
  };

  /** The problem with the chosen files and links as they stand, if there is one. */
  const checkPicked = (list: File[], linkList: string[]): void => {
    if (list.length + linkList.length > IMPORT_MAX_FILES) {
      setPickProblem(words.tooManyFiles);
      return;
    }
    const big = list.find((f) => f.size > IMPORT_MAX_MB * 1024 * 1024);
    // Links are told apart from files by the name "Google Doc N.txt", so a file may not use it.
    const clash = list.find((f) => /^Google Doc \d+\.txt$/.test(f.name));
    const odd = list.find((f) => !f.name.toLowerCase().endsWith(".txt"));
    setPickProblem(
      big
        ? words.fileTooBig(big.name)
        : clash
          ? words.fileNameTaken(clash.name)
          : odd
          ? words.fileNotTxt(odd.name)
          : null,
    );
  };

  const choose = (event: Event): void => {
    if (busy) return;
    const list = Array.from((event.target as HTMLInputElement).files ?? []);
    setPicked(list);
    forgetPreview();
    checkPicked(list, links);
  };

  const addLink = (): void => {
    const link = linkText.trim();
    if (link === "" || busy) return;
    // Caught here so a typo does not cost a round trip. The API still checks (not_a_google_doc).
    if (!/^(https?:\/\/)?docs\.google\.com\/document\//i.test(link)) {
      setLinkProblem(words.notAGoogleDocLink);
      return;
    }
    // The same doc can be pasted with a different ending, so compare the doc's id when there is one.
    const docId = (l: string): string => /\/d\/([\w-]+)/.exec(l)?.[1] ?? l;
    if (links.some((l) => docId(l) === docId(link))) {
      setLinkProblem(words.linkAlreadyAdded);
      return;
    }
    if (picked.length + links.length >= IMPORT_MAX_FILES) {
      setLinkProblem(words.tooManyToAdd);
      return;
    }
    const next = [...links, link];
    setLinks(next);
    setLinkText("");
    setLinkProblem(null);
    forgetPreview();
    checkPicked(picked, next);
    setAnnounce(words.added(words.linkName(next.length), picked.length + next.length));
    linkBox.current?.focus();
  };

  const removeLink = (at: number): void => {
    if (busy) return;
    const next = links.filter((_, i) => i !== at);
    setLinks(next);
    setLinkProblem(null);
    forgetPreview();
    checkPicked(picked, next);
    setAnnounce(words.removed(words.linkName(at + 1), picked.length + next.length));
    // The button that was pressed is gone, so focus goes to the next Remove, or to the box.
    requestAnimationFrame(() => {
      const buttons = document.querySelectorAll<HTMLButtonElement>("[data-remove-link]");
      (buttons[Math.min(at, buttons.length - 1)] ?? linkBox.current)?.focus();
    });
  };

  const lookAtFiles = (): Promise<void> =>
    run(async () => {
      const files: ImportFileIn[] = await Promise.all(
        picked.map(async (f) => ({ name: f.name, text: await f.text() })),
      );
      const found = await api.importPreview!(ref, files, links);
      const good = found.files.filter((f) => f.error === null);
      // A link that did not open is left out from here on, so the ones that did are renamed by place.
      const opened: string[] = [];
      const names: Record<string, string> = {};
      let unanswered = false;
      links.forEach((link, i) => {
        const shown = `${words.linkName(i + 1)}.txt`;
        const answer = found.files.find((f) => f.name === shown);
        // An older API ignores `links`; a link it never answered must not just vanish.
        if (!answer) unanswered = true;
        if (answer?.error === null) {
          opened.push(link);
          names[shown] = `${words.linkName(opened.length)}.txt`;
        }
      });
      if (unanswered) setProblem(words.docNoAnswer);
      setSentLinks(opened);
      setSentNames(names);
      setTexts(files);
      setPreview(found);
      setCheck(null);
      const start: Record<string, string> = {};
      for (const f of good)
        for (const s of f.speakers)
          start[s.label] ??= s.suggested?.userId ?? DROP;
      setMapping(start);
      setDates(
        Object.fromEntries(good.map((f) => [f.name, f.suggestedDate ?? ""])),
      );
      setGuessed(
        Object.fromEntries(good.map((f) => [f.name, f.suggestedDate !== null])),
      );
      setOrder(good.map((f) => f.name));
      const suggestedPeople: ImportMember[] = good.flatMap((f) =>
        f.speakers.flatMap((s) =>
          // `agreed` is not invented: only the members list and Check know it (consent is theirs to say).
          s.suggested
            ? [
                // A remembered suggestion with no name shows as the label, not a blank option.
                {
                  userId: s.suggested.userId,
                  name: s.suggested.name.trim() || s.label,
                },
              ]
            : [],
        ),
      );
      remember(suggestedPeople);
      if (api.importMembers) remember(await api.importMembers(ref, ""));
    });

  const search = (value: string): void => {
    setQuery(value);
    if (!api.importMembers) return;
    // Wait for a pause in the typing, and use only the newest answer: a slow answer for "ab" must
    // not land after "abc".
    const mine = ++searchSeq.current;
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      void api.importMembers!(ref, value).then(
        (list) => mine === searchSeq.current && remember(list),
        () => undefined, // The people already listed still work; the next typing tries again.
      );
    }, 300);
  };

  // The API orders the sessions by plan.sessions[].order, not by the order the files are sent in.
  const sentFiles = (): ImportFileIn[] =>
    texts.filter((t) => order.includes(t.name));

  const doCheck = (): Promise<void> =>
    run(async () => {
      if (!api.importCheck) return;
      setCheck(await api.importCheck(ref, sentFiles(), mapping, sentLinks));
    });

  const move = (name: string, by: -1 | 1): void =>
    setOrder((now) => {
      const i = now.indexOf(name);
      const j = i + by;
      if (i < 0 || j < 0 || j >= now.length) return now;
      const next = [...now];
      [next[i], next[j]] = [next[j] as string, next[i] as string];
      setMoved(words.moved(words.shownName(name), j + 1, now.length));
      return next;
    });

  const allDates = order.every((n) => isDate(dates[n] ?? ""));
  // The choice is offered while recaps are on for the site and only where the API says the bot
  // would write them (#2229, from the preview): never a tick it would refuse.
  const offerRecaps = RECAPS_LIVE && preview?.recapsPossible === true;
  const recapsWhy = RECAPS_LIVE && preview?.recapsPossible === false ? preview.recapsWhy : undefined;
  const allRecaps = order.length > 0 && order.every((n) => recaps[n] === true);
  const someRecaps = order.some((n) => recaps[n] === true);

  const start = (): Promise<void> =>
    run(async () => {
      if (!api.importStart) return;
      // A Google Doc goes by the name it will have in the list that is sent now.
      const sentName = (file: string): string => sentNames[file] ?? file;
      const sentDates = Object.fromEntries(
        order.map((file) => [sentName(file), dates[file] ?? ""]),
      );
      const started = await api.importStart(
        ref,
        sentFiles(),
        {
          mapping,
          sessions: order.map((file, i) => ({
            file: sentName(file),
            date: dates[file] ?? "",
            order: i + 1,
            // Only a ticked session says anything about recaps.
            ...(offerRecaps && recaps[file] ? { writeRecap: true as const } : {}),
          })),
        },
        sentLinks,
      );
      // The setting can change between Preview and the start: the tick was sent but the bot will
      // decline it, and the progress view says so instead of implying a recap is coming.
      if (started.recapsPossible === false && offerRecaps && someRecaps)
        setStartedWithoutRecaps(started.recapsWhy ?? "unknown");
      setProgress({
        sessions: started.sessions.map((s) => ({
          file: s.file,
          date: sentDates[s.file] ?? "",
          state: s.state,
          reason: null,
        })),
      });
      setImportId(started.importId);
    });

  const memberOptions = Object.values(known);
  const label = (m: ImportMember): string =>
    m.agreed === false ? `${m.name} (${words.notAgreedMark})` : m.name;

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
    const done = progress.sessions.every(isFinal);
    const failed = progress.sessions.some(isFailed);
    const unchecked = progress.sessions.some((s) => s.namesUnchecked === true);
    return (
      <div class="stack">
        <h2>{words.progressHeading}</h2>
        {problem && (
          <p class="note bad" role="alert">
            {problem}
          </p>
        )}
        {problem && (
          <button
            type="button"
            class="button secondary"
            onClick={() => {
              setProblem(null);
              setPollTries((n) => n + 1);
            }}
          >
            {words.checkAgain}
          </button>
        )}
        {startedWithoutRecaps && (
          <p class="small" role="status">
            {words.recapsNotWritten}{" "}
            <RecapsWhyLine why={startedWithoutRecaps} />
          </p>
        )}
        <p role="status" aria-live="polite">
          {words.doneCount(
            progress.sessions.filter((s) => isFinal(s) && !isFailed(s)).length,
            progress.sessions.length,
          )}
        </p>
        <ul class="campaigns">
          {progress.sessions.map((s) => (
            <li key={s.file}>
              <span class="campaign-name">{shownApi(s.file)}</span>
              <span class="small">
                {s.date} · {stageLine(s)}
                {/* A reason can come with any state: "done" can still say some lines were not sorted. */}
                {reasonLine(s.state, s.reason)
                  ? ` · ${reasonLine(s.state, s.reason)}`
                  : ""}
              </span>
              {s.stage === "waiting_for_names" && (
                <a
                  class="small"
                  href={recapHref(s)}
                  aria-label={words.linkFor(words.namesLink, shownApi(s.file))}
                >
                  {words.namesLink}
                </a>
              )}
              {(s.stage === "recap_ready" || s.stage === "recap_failed") && (
                <a
                  class="small"
                  // A failed recap may have no page of its own to open; its words say "recap list".
                  href={s.stage === "recap_failed" ? "/account/recaps" : recapHref(s)}
                  aria-label={words.linkFor(words.recapLink, shownApi(s.file))}
                >
                  {words.recapLink}
                </a>
              )}
            </li>
          ))}
        </ul>
        {unchecked && <p class="small muted">{words.namesUnchecked}</p>}
        {done && (
          <p role="status">
            {failed ? words.someFailed : words.allDone}{" "}
            <a href={`/account/transcripts/${encodeURIComponent(ref)}`}>
              {words.seeTranscripts}
            </a>
          </p>
        )}
      </div>
    );
  }

  return (
    <div class="stack">
      <p class="muted small">{words.lead}</p>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}

      {pickProblem && (
        <p class="note bad" role="alert">
          {pickProblem}
        </p>
      )}

      <section class="panel" aria-labelledby="import-pick">
        <h2 id="import-pick">{words.pickLabel}</h2>
        <input
          type="file"
          accept=".txt,text/plain"
          multiple
          onChange={choose}
          disabled={busy}
          aria-label={words.pickLabel}
        />
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            addLink();
          }}
        >
          <label class="field">
            {words.linkLabel}
            <input
              ref={linkBox}
              type="url"
              value={linkText}
              placeholder={words.linkPlaceholder}
              autoComplete="off"
              spellcheck={false}
              aria-describedby="import-link-help"
              onInput={(e) => setLinkText((e.target as HTMLInputElement).value)}
            />
          </label>
          <button
            type="submit"
            class="button secondary"
            disabled={linkText.trim() === "" || busy}
          >
            {words.addLink}
          </button>
        </form>
        {linkProblem && (
          <p class="note bad" role="alert">
            {linkProblem}
          </p>
        )}
        <div class="muted small" id="import-link-help">
          <p>{words.linkHelp}</p>
          <ol>
            {words.linkSteps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <p>{words.linkHelpAfter}</p>
        </div>
        <p class="sr-only" role="status">
          {announce}
        </p>
        {links.length > 0 && (
          <ul class="campaigns">
            {links.map((link, i) => (
              <li key={`${i}-${link}`}>
                <span class="campaign-name">{words.linkName(i + 1)}</span>
                <span class="small">{words.shortLink(link)}</span>
                <button
                  type="button"
                  data-remove-link
                  disabled={busy}
                  class="button secondary"
                  aria-label={words.removeLink(i + 1)}
                  onClick={() => removeLink(i)}
                >
                  {words.remove}
                </button>
              </li>
            ))}
          </ul>
        )}
        {picked.length + links.length > 0 && (
          <p class="small">
            {links.length > 0
              ? words.chosenItems(picked.length, links.length)
              : words.chosen(picked.length)}
          </p>
        )}
        <button
          type="button"
          class="button"
          disabled={
            busy ||
            picked.length + links.length === 0 ||
            picked.length + links.length > IMPORT_MAX_FILES ||
            // Only a problem with the chosen files blocks Preview. An answer from the API (a busy
            // moment, a refusal) must not leave it locked: the DM can press it again.
            pickProblem !== null
          }
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
                  <span class="campaign-name">{words.shownName(f.name)}</span>
                  <span class="small">
                    {words.fileError[f.error ?? ""] ?? words.fileErrorOther}
                  </span>
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
                    onInput={(e) =>
                      search((e.target as HTMLInputElement).value)
                    }
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
                          {s.why && s.suggested === chosen
                            ? ` · ${words.suggestedBecause(s.why)}`
                            : ""}
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
                        {who && who.agreed === false && (
                          <span class="small" role="note">
                            {words.droppedReason.not_agreed}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  onClick={() => void doCheck()}
                >
                  {words.checkButton}
                </button>
                {/* A live region that is always there, so the result is announced when it fills in. */}
                <div role="status" aria-live="polite">
                  {check && (
                    <>
                      <p>
                        {words.checkedSummary(
                          check.files.reduce(
                            (n, f) =>
                              n + f.speakers.reduce((m, x) => m + x.kept, 0),
                            0,
                          ),
                          check.files.reduce(
                            (n, f) =>
                              n +
                              f.speakers.reduce(
                                (m, x) =>
                                  m +
                                  Object.values(x.dropped).reduce(
                                    (d, v) => d + v,
                                    0,
                                  ),
                                0,
                              ),
                            0,
                          ),
                        )}
                      </p>
                      <p class="small">{words.checkLead}</p>
                      <ul class="campaigns">
                        {check.files.map((f) => (
                          <li key={f.name}>
                            <span class="campaign-name">{shownApi(f.name)}</span>
                            {f.error !== null && (
                              <span class="small" role="alert">
                                {words.fileError[f.error] ?? words.fileErrorOther}
                              </span>
                            )}
                            {f.leftOut > 0 && (
                              <span class="small">
                                {words.leftOutFile(f.leftOut)}
                              </span>
                            )}
                            <ul>
                              {f.speakers.map((x) => (
                                <li class="small" key={x.label}>
                                  {x.label}: {words.kept(x.kept)}
                                  {Object.entries(x.dropped)
                                    .filter(([, n]) => n > 0)
                                    .map(
                                      ([why, n]) =>
                                        `; ${words.dropped(n, words.droppedReason[why] ?? why)}`,
                                    )
                                    .join("")}
                                </li>
                              ))}
                            </ul>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              </section>

              <section class="panel" aria-labelledby="import-dates">
                <h2 id="import-dates">{words.datesHeading}</h2>
                <p class="small">{words.datesLead}</p>
                {order.length > 1 &&
                  allDates &&
                  order.some(
                    (n, i) =>
                      i > 0 &&
                      (dates[n] ?? "") < (dates[order[i - 1] as string] ?? ""),
                  ) && (
                    <p class="note" role="note">
                      {words.datesOutOfOrder}
                    </p>
                  )}
                <p class="sr-only" role="status" aria-live="polite">
                  {moved}
                </p>
                <ol class="campaigns">
                  {order.map((name, i) => (
                    <li key={name}>
                      <span class="campaign-name">{words.shownName(name)}</span>
                      <span class="small">
                        {words.position(i + 1, order.length)}
                      </span>
                      <label class="field">
                        {words.dateLabel(words.shownName(name))}
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
                      {guessed[name] && (
                        <span class="small">{words.dateGuess}</span>
                      )}
                      <span class="row">
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={words.moveUp(words.shownName(name))}
                          aria-disabled={i === 0}
                          onClick={() => move(name, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={words.moveDown(words.shownName(name))}
                          aria-disabled={i === order.length - 1}
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
                <p>
                  {words.estimate(
                    order.length,
                    // Once Check has run, its estimate is for the same choices; use it.
                    (check?.estimate ?? preview.estimate).fastCalls,
                  )}
                </p>
                {recapsWhy && words.recapsWhy[recapsWhy] && order.length > 0 && (
                  <p class="small" role="note">
                    <RecapsWhyLine why={recapsWhy} />
                  </p>
                )}
                {offerRecaps && order.length > 0 && (
                  <fieldset class="recap-choices">
                    <legend>{words.recapsHeading}</legend>
                    <label class="choice">
                      <input
                        type="checkbox"
                        checked={allRecaps}
                        ref={(el) => {
                          if (el) el.indeterminate = someRecaps && !allRecaps;
                        }}
                        disabled={busy}
                        onChange={(e) =>
                          setRecaps(
                            Object.fromEntries(
                              order.map((n) => [n, e.currentTarget.checked]),
                            ),
                          )
                        }
                      />
                      <span>{words.recapsAll}</span>
                    </label>
                    {order.map((n) => (
                      <label class="choice" key={`recap-${n}`}>
                        <input
                          type="checkbox"
                          checked={recaps[n] === true}
                          disabled={busy}
                          onChange={(e) => {
                            const on = e.currentTarget.checked;
                            setRecaps((now) => ({ ...now, [n]: on }));
                          }}
                        />
                        <span>{words.recapsOne(words.shownName(n))}</span>
                      </label>
                    ))}
                    <p class="small">{words.recapsNote}</p>
                  </fieldset>
                )}
                <p class="small">{words.confirmNote}</p>
                <p class="small">{words.renumberNote}</p>
                <button
                  type="button"
                  class="button"
                  disabled={
                    busy || !allDates || order.length === 0 || check === null
                  }
                  onClick={() => void start()}
                >
                  {busy ? words.working : words.importButton}
                </button>
                {check === null && <p class="small">{words.checkFirst}</p>}
                {check !== null && !allDates && (
                  <p class="small">{words.needDates}</p>
                )}
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}

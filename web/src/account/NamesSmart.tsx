/** @jsxImportSource preact */
// "Search by meaning" on the Names page (#2733): a separate file the page fetches only when the switch is on and
// the viewer is the campaign's DM (LazyPanel), so the first download stays light. It searches only when Search
// or Enter is pressed (each search costs a call), hands the closest names to the list (`onResults`), and the list
// stays the page's. The words typed live in this box and in `lastSearch` only: never in an address, a log or the
// call log. A late answer is ignored (a newer search, Back, or the list's own controls make it stale), the wait
// says what it is doing and goes quiet after a minute with a way on, and a failure says what to do next.
import { useEffect, useRef, useState } from "preact/hooks";

import { namesPage as words } from "../content/names-words";
import { smartWords } from "../content/names-smart-words";
import { type AccountApi, ApiError, type NameRow } from "./api";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

export interface NamesSmartProps {
  api: AccountApi;
  campaignRef: string;
  /** The closest names, or null to go back to the list. */
  onResults: (names: NameRow[] | null) => void;
  /** Whether the list is showing search results now. */
  showing: boolean;
  /** Bumped when a name changed: the search is read again (quietly) so its rows are not out of date. */
  refreshKey: number;
  /** Bumped when the list's own controls were used: this search is over. */
  leaveKey: number;
  /** How many names are showing, for the count line. */
  count: number;
  /** The session ended during a search. */
  onSignedOut: () => void;
}

/** The words for a meaning search that did not work. */
function problemFor(kind: string): string {
  if (kind === "not-available") return smartWords.notReady;
  if (kind === "network") return words.errors.network ?? smartWords.failed;
  return smartWords.failed;
}

export default function NamesSmart({ api, campaignRef, onResults, showing, refreshKey, leaveKey, count, onSignedOut }: NamesSmartProps) {
  const [meaning, setMeaning] = useState("");
  const [run, setRun] = useState<{ since: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [capped, setCapped] = useState(false); // only the newest notes were searched (the API says so)
  const latest = useRef(0);
  const lastSearch = useRef("");
  const shownRef = useRef(false); // (a ref: an answer that arrives later must see it now, not as of its render)
  const box = useRef<HTMLInputElement>(null);
  // The search gives no progress, so the wait shows what the page saw: how long, and "nothing new" after a minute.
  const seen = useObservedWait(run !== null, "searching");

  async function search(typed: string): Promise<void> {
    const text = typed.trim();
    if (!api.smartNames) return;
    // A search already running is ignored, unless it has gone quiet: then a new press starts over.
    if (run !== null && !seen.quiet) return;
    if (text === "") {
      setProblem(smartWords.typeSome);
      return;
    }
    if (text.length > 100) {
      setProblem(smartWords.tooLong);
      return;
    }
    const mine = ++latest.current;
    lastSearch.current = text;
    setProblem(null);
    setRun({ since: Date.now() / 1000 });
    try {
      const got = await api.smartNames(campaignRef, text);
      if (mine !== latest.current) return;
      shownRef.current = true;
      setCapped(got.capped === true);
      onResults(got.names);
    } catch (error) {
      if (mine !== latest.current) return;
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") onSignedOut();
      else setProblem(problemFor(kind));
    } finally {
      if (mine === latest.current) setRun(null);
    }
  }

  /** The search is over: an answer still on its way is no longer wanted, and the words are forgotten. */
  function leave(): void {
    latest.current++;
    shownRef.current = false;
    lastSearch.current = "";
    setRun(null);
    setProblem(null);
    setCapped(false);
  }

  // The list's own controls were used: back to the list. (Not on the first render.)
  const firstLeave = useRef(true);
  useEffect(() => {
    if (firstLeave.current) {
      firstLeave.current = false;
      return;
    }
    if (shownRef.current || run !== null) leave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaveKey]);

  // A name changed: read the same search again, quietly.
  const firstRefresh = useRef(true);
  useEffect(() => {
    if (firstRefresh.current) {
      firstRefresh.current = false;
      return;
    }
    if (!shownRef.current || lastSearch.current === "" || !api.smartNames) return;
    const mine = latest.current;
    api.smartNames(campaignRef, lastSearch.current).then(
      (got) => {
        if (mine === latest.current && shownRef.current) {
          setCapped(got.capped === true);
          onResults(got.names);
        }
      },
      () => {
        // (the rows stay as they were; the next Search says what is wrong)
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  function back(): void {
    leave();
    setMeaning("");
    onResults(null);
    // The button that was pressed is gone: the keyboard goes to the box, ready for the next search.
    setTimeout(() => box.current?.focus(), 0);
  }

  return (
    <form
      class="names-smart stack"
      role="search"
      aria-label={smartWords.region}
      onSubmit={(e) => {
        e.preventDefault();
        void search(meaning);
      }}
      noValidate
    >
      <label class="field">
        {smartWords.label}
        <input
          type="search"
          name="meaning"
          value={meaning}
          autoComplete="off"
          spellcheck={false}
          ref={box}
          aria-describedby="names-smart-hint"
          onInput={(e) => setMeaning((e.target as HTMLInputElement).value)}
        />
      </label>
      <p id="names-smart-hint" class="small muted">
        {smartWords.hint}
      </p>
      <div class="row">
        {/* aria-disabled, not disabled: pressing it must not drop the keyboard (a search already running is
            ignored, and an empty one says what to type). */}
        <button type="submit" class="button" aria-disabled={run !== null && !seen.quiet}>
          {smartWords.button}
        </button>
        {showing && (
          <button type="button" class="button secondary" onClick={back}>
            {smartWords.back}
          </button>
        )}
      </div>
      {showing && <p class="small muted">{smartWords.showing}</p>}
      {showing && capped && <p class="small muted">{smartWords.capped}</p>}
      {showing && (
        <p class="small" role="status" aria-live="polite">
          {count === 0 ? smartWords.none : smartWords.found(count)}
        </p>
      )}
      {run !== null && (
        <WorkProgress
          stage={smartWords.stage}
          state={seen.quiet ? "stalled" : "running"}
          startedAt={seen.startedAt ?? run.since}
          updatedAt={seen.movedAt}
          whatToDo={seen.quiet ? smartWords.quiet : undefined}
          progressUnknown
        />
      )}
      {problem !== null && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </form>
  );
}

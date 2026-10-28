/** @jsxImportSource preact */
// The Names page (#2295): every name DMbot remembers in one campaign, for a big screen and usable
// on a phone. Anyone the API lets in can search (as you type), filter by kind, sort and page; the
// campaign's DMs can also fix a name's spelling, change its kind, add or remove other names, mark it
// secret, merge it into another name or remove it (the same actions as the name card in Discord, so
// Undo there still works). Who sees what is the API's rule, never the page's: a server manager who
// isn't one of the DMs gets the list without secret names and no way to change anything, and anyone
// else gets "not found". The page only shows what it is given.
import { useEffect, useRef, useState } from "preact/hooks";

import PageBar from "../admin/PageBar";
import { NAME_LIMIT, NAMES_PAGE_SIZE, NAMES_PAGE_SIZES, NAMES_SMART_LIVE, namesPage as words } from "../content/names-words";
import { NOT_NAMED_LIVE } from "../content/not-named-switch";
import { notNamedWords as notNamed } from "../content/not-named-words";
import {
  type AccountApi,
  ApiError,
  type NameChange,
  type NameKind,
  type NameRow,
  type NamesPage,
  type NameSort,
} from "./api";
import CampaignNav from "./CampaignNav";
import NameThem, { type NamedInfo } from "./NameThem";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";
import SayIt from "./SayIt";
import LazyPanel from "./LazyPanel";

/** The campaign's address, read from `/account/names/<server>.<campaign>`. */
export function namesRefFromPath(path: string): string | null {
  const match = /^\/account\/names\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null; // a broken %-escape in the address is just a campaign that isn't there
  }
}

type View = "loading" | "ok" | "none" | "down" | "signed-out";

const SEARCH_MS = 250;

/** The words for a refusal, or null when it means the page should react instead (signed out). */
function refusal(error: unknown): string | null {
  const kind = error instanceof ApiError ? error.kind : "network";
  if (kind === "signed-out") return null;
  return words.errors[kind] ?? words.errorFallback;
}

/** The name Discord's "🎤 Say it" button sent the DM here for, from `#said=<the name as written>` (#2317): the
 * page opens on a search for it and, when one name is exactly that, opens its editor with the cursor in the
 * "how to say it" box. Anything else (no such name, a long or odd value) just shows the plain list. It is in
 * the address's fragment, not its query: a name can be a secret, and a query string is written to the site's
 * logs while a fragment never leaves the browser. */
export function saidFromHash(hash: string): string {
  if (!hash.startsWith("#")) return ""; // (only a fragment is read, never a query string)
  const raw = new URLSearchParams(hash.replace(/^#/, "")).get("said")?.trim() ?? "";
  return raw.length > 0 && raw.length <= 80 ? raw : "";
}

export default function Names({ api, path, hash = "" }: { api: AccountApi; path: string; hash?: string }) {
  const ref = namesRefFromPath(path);
  const sentFor = useRef(saidFromHash(hash)); // the name the link was for, until it has been opened
  const [focusRow, setFocusRow] = useState<string | null>(null); // the row whose "how to say it" box takes the cursor
  const [view, setView] = useState<View>("loading");
  const [data, setData] = useState<NamesPage | null>(null);
  const [q, setQ] = useState(() => saidFromHash(hash));
  // Once read, the name leaves the address bar, so it is not left in the history or shared by a screenshot.
  useEffect(() => {
    if (saidFromHash(hash) !== "" && typeof window !== "undefined" && window.location.hash !== "") {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // The types ticked (none ticked is every type), and how many names a page holds (#2295, owner
  // request 2026-10-27: compact rows, so a page can be long).
  const [kinds, setKinds] = useState<string[]>([]);
  const kind = kinds.join(",");
  const [size, setSize] = useState<number>(NAMES_PAGE_SIZE);
  const [sort, setSort] = useState<NameSort>("name");
  // "Search by meaning" (#2733) is a separate file (NamesSmart, fetched only when it is on and for a DM). Here is
  // only what the list needs of it: the names it found (replacing the list until "Back to all names"), and two
  // counters that tell it a name changed (read the search again) or the list's own controls were used (it is over).
  const [smart, setSmart] = useState<NameRow[] | null>(null);
  const [smartRefresh, setSmartRefresh] = useState(0);
  const [smartLeave, setSmartLeave] = useState(0);
  const smartNow = useRef<NameRow[] | null>(null);
  smartNow.current = smart;
  const [busy, setBusy] = useState(false);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [news, setNews] = useState<{ ok: boolean; text: string; n: number } | null>(null);
  // A name just given (#2803) can be taken back until the next change; `undoing` is the wait for the bot.
  const [undo, setUndo] = useState<{ answer: string; rowId: string; join: boolean } | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [undoLooks, setUndoLooks] = useState(0);
  const undoFlight = useRef(false);
  const undoSeen = useObservedWait(undoing, String(undoLooks));
  // Only the newest read may set what is shown, so a slow old answer never overwrites a new one.
  const latest = useRef(0);
  const pageNow = useRef(1);
  const queryNow = useRef({ q, kind, sort, size });
  queryNow.current = { q, kind, sort, size };
  // Names ticked for "Merge selected" (#2427): kept across searches and pages, so look-alikes found
  // by different searches can be merged together.
  const [picked, setPicked] = useState<NameRow[]>([]);
  const [merging, setMerging] = useState(false);
  const [keepId, setKeepId] = useState("");
  const [mergeBusy, setMergeBusy] = useState(false);
  // Set at once, unlike state: a second press in the same moment must not send a second request.
  const mergeFlight = useRef(false);
  const mergeHeading = useRef<HTMLHeadingElement>(null);
  const mergeOpener = useRef<HTMLButtonElement>(null);
  const newsBox = useRef<HTMLParagraphElement>(null);
  const first = useRef(true);
  const sayings = useRef(0);

  /** Reads one page and says which page is showing afterwards (for the page bar). */
  async function load(page: number, silent = false): Promise<number> {
    if (!ref || !api.allNames) return pageNow.current;
    const mine = ++latest.current;
    setBusy(true);
    const asked = queryNow.current;
    try {
      const read = await api.allNames(ref, { ...asked, page });
      if (mine !== latest.current) return pageNow.current;
      setData(read);
      setView("ok");
      // Ticks are kept across searches and pages, but a ticked name shown again here is refreshed
      // (a new spelling must not stay old in the question), and nothing stays ticked for someone
      // who may no longer change names.
      setPicked((now) =>
        read.canEdit ? now.map((t) => read.names.find((n) => n.id === t.id) ?? t) : [],
      );
      // An editor stays open only for a row still on the page: a search, filter or page change that
      // drops it must not bring it back, with old words in it, when the row returns (#2299).
      setEditing((open) => (open !== null && read.names.some((n) => n.id === open) ? open : null));
      // Opened from Discord's "Say it" link: the one name that is exactly the name sent, opened once.
      if (sentFor.current && read.canEdit) {
        const wanted = sentFor.current.toLocaleLowerCase();
        const exact = read.names.filter((n) => n.aliasId && n.name.toLocaleLowerCase() === wanted);
        sentFor.current = ""; // (only the first read: a later search must not open anything)
        if (exact.length === 1 && api.saveSaidAs) {
          setEditing(exact[0]!.id);
          setFocusRow(exact[0]!.id);
        }
      }
      pageNow.current = read.page;
      setPageNotice(null);
      return read.page;
    } catch (error) {
      if (mine !== latest.current) return pageNow.current;
      const kindOf = error instanceof ApiError ? error.kind : "network";
      if (kindOf === "signed-out") setView("signed-out");
      else if (kindOf === "not-found") setView("none");
      else if (kindOf === "bad-page" && page !== 1) {
        // The list got shorter: the first page is always there.
        const shown = await load(1, true);
        if (pageNow.current === shown) setPageNotice((now) => now ?? words.pageGone);
        return shown;
      } else if (silent || data) setPageNotice(words.pageDown);
      else setView("down");
      return pageNow.current;
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

  // Search as you type, filter and sort: a new read from page 1 (the first one at once).
  useEffect(() => {
    if (!ref || !api.allNames) return;
    const wait = first.current ? 0 : SEARCH_MS;
    first.current = false;
    setNews(null);
    if (smartNow.current !== null) {
      // (typing in Find a name, or a type, sort or size, is back to the list)
      setSmart(null);
      setSmartLeave((n) => n + 1);
    }
    const timer = setTimeout(() => void load(1), wait);
    return () => clearTimeout(timer);
    // `load` reads the latest query through a ref, so only these restart the read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref, q, kind, sort, size]);

  // The most-mentioned name first, as the one to keep by default.
  const ordered = [...picked].sort(
    (a, b) => (b.mentions ?? 0) - (a.mentions ?? 0) || a.name.localeCompare(b.name),
  );

  useEffect(() => {
    if (merging) mergeHeading.current?.focus();
  }, [merging]);

  // Fewer than two ticked: the question is gone with the bar, and must not come back by itself.
  useEffect(() => {
    if (picked.length < 2) setMerging(false);
  }, [picked.length]);

  function pick(row: NameRow): void {
    setPicked((now) => (now.some((r) => r.id === row.id) ? now.filter((r) => r.id !== row.id) : [...now, row]));
  }

  function openMerge(): void {
    setKeepId(ordered[0]?.id ?? "");
    setNews(null);
    setMerging(true);
  }

  function closeMerge(): void {
    setMerging(false);
    setTimeout(() => mergeOpener.current?.focus(), 0);
  }

  /** Every ticked name into the one kept: one queued change, waited for like the others (#2427). */
  async function mergeMany(): Promise<void> {
    if (!ref || !api.changeName || !data?.canEdit || mergeFlight.current) return;
    const keep = ordered.find((r) => r.id === keepId) ?? ordered[0];
    if (!keep) return;
    const others = ordered.filter((r) => r.id !== keep.id);
    mergeFlight.current = true;
    setMergeBusy(true);
    try {
      await api.changeName(ref, keep.id, { action: "merge-many", others: others.map((r) => r.id) });
      setPicked([]);
      setMerging(false);
      setNews({ ok: true, text: words.mergedMany(keep.name), n: ++sayings.current });
      setTimeout(() => newsBox.current?.focus(), 0);
      await reload();
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return;
      }
      const refused = error instanceof ApiError && error.kind === "bad-target";
      setNews({ ok: false, text: refused ? words.mergeManyRefused : said, n: ++sayings.current });
      if (error instanceof ApiError && (error.kind === "still-waiting" || error.kind === "not-found")) {
        // It may have happened, or a name is gone: read the list again and start the ticking over.
        setPicked([]);
        setMerging(false);
        await reload();
      }
    } finally {
      mergeFlight.current = false;
      setMergeBusy(false);
    }
  }

  /** A person was given a name (#2803): say so, offer Undo, close the editor and read the list again. */
  async function named(row: NameRow, info: NamedInfo): Promise<void> {
    setNews({ ok: true, text: info.text, n: ++sayings.current });
    setUndo(info.undo ? { answer: info.undo, rowId: row.id, join: info.join } : null);
    setEditing(null);
    setTimeout(() => newsBox.current?.focus(), 0);
    await reload();
  }

  async function undoNamed(): Promise<void> {
    if (!undo || !ref || !api.nameThem || undoFlight.current) return;
    undoFlight.current = true;
    setUndoing(true);
    setUndoLooks(0);
    try {
      await api.nameThem(ref, undo.rowId, { action: "name-undo", answer: undo.answer }, () => setUndoLooks((n) => n + 1));
      setUndo(null);
      setNews({ ok: true, text: notNamed.undone, n: ++sayings.current });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") return void setView("signed-out");
      // It may still be applied: a second press would queue a second undo of the same step.
      if (kind === "still-waiting") setUndo(null);
      setNews({ ok: false, text: notNamed.undoProblems[kind] ?? notNamed.undoProblems["server"]!, n: ++sayings.current });
    } finally {
      undoFlight.current = false;
      setUndoing(false);
    }
    await reload();
  }

  /** The list as it is now, and the search results too when they are showing (they would be out of date). */
  function reload(): Promise<unknown> {
    const read = load(pageNow.current, true);
    // (no extra step when no search is showing: the editor stays busy only as long as the list read takes)
    if (smartNow.current !== null) void read.then(() => setSmartRefresh((n) => n + 1)); // (the search is read again)
    return read;
  }

  /** One change to one name. Resolves with the refusal's words, or null when it worked. */
  async function act(row: NameRow, change: NameChange, ok: string): Promise<string | null> {
    if (!ref || !api.changeName) return words.errorFallback;
    setUndo(null); // a later change ends the chance to undo a name just given
    try {
      await api.changeName(ref, row.id, change);
      // A name that has gone (removed, or merged into another) is not ticked any more.
      if (change.action === "remove" || change.action === "merge") {
        setPicked((now) => now.filter((t) => t.id !== row.id));
      }
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      if (error instanceof ApiError && error.kind === "still-waiting") {
        // It may still happen: say so, and read the list again so it shows if it did.
        setNews({ ok: false, text: said, n: ++sayings.current });
        await reload();
        return null;
      }
      if (error instanceof ApiError && error.kind === "not-found") {
        // The name is gone (someone else removed it): the list is read again and says so.
        setEditing(null);
        setNews({ ok: false, text: said, n: ++sayings.current });
        // The editor is gone: the keyboard goes to the news, as after a remove or a merge.
        setTimeout(() => newsBox.current?.focus(), 0);
        await reload();
        return null;
      }
      return said;
    }
    setNews({ ok: true, text: ok, n: ++sayings.current });
    if (change.action === "remove" || change.action === "merge") {
      setEditing(null);
      // The editor is gone: the keyboard goes to the news, where the result is said.
      setTimeout(() => newsBox.current?.focus(), 0);
    }
    await reload();
    return null;
  }

  if (!ref || !api.allNames) return <p role="status">{words.notYours}</p>;
  if (view === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{words.signedOut}</p>
      </div>
    );
  if (view === "none") return <p role="status">{words.notYours}</p>;
  if (view === "down")
    return (
      <div class="stack">
        <p class="note bad" role="alert">
          {words.down}
        </p>
        <button type="button" class="button secondary" onClick={() => void load(1)}>
          {words.tryAgain}
        </button>
      </div>
    );
  if (view === "loading" || !data)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  // The API never sends a secret name to someone who isn't a DM; this keeps one from showing if
  // it ever did (the rule is the API's, this is only a backstop).
  // "Not named yet" is a DM's, and only while the switch is on (the API sends it to a DM only; this is the backstop).
  const unnamedOn = NOT_NAMED_LIVE && data.canEdit && api.nameThem !== undefined;
  const shown = (smart ?? data.names).filter((n) => (data.canEdit || !n.secret) && (unnamedOn || !n.unnamed));
  const kindList = unnamedOn ? data.kinds : data.kinds.filter((k) => k.key !== "not_named");
  const smartOn = NAMES_SMART_LIVE && data.canEdit && api.smartNames !== undefined;
  const everything = kindList.reduce((sum, k) => sum + k.count, 0);
  return (
    <div class="stack names-page">
      <section class="stack" aria-labelledby="names-campaign">
        <CampaignNav api={api} campaignRef={ref} page="names" name={data.campaign} />
        <h2 id="names-campaign">{data.campaign}</h2>
        <p class="muted small">{words.lead}</p>
        <a class="button secondary" href={`/account/web/${encodeURIComponent(ref)}`}>
          {words.webLink}
        </a>
        {data.canEdit ? (
          <p class="small muted">
            <span id="names-press-hint">{words.pressName}</span> {words.undoNote}
          </p>
        ) : (
          <p class="note" role="note">
            {words.readOnly}
          </p>
        )}
      </section>

      <form class="names-tools" role="search" onSubmit={(e) => e.preventDefault()} noValidate>
        <label class="field">
          {words.searchLabel}
          <input
            type="search"
            name="q"
            value={q}
            autoComplete="off"
            spellcheck={false}
            aria-describedby="names-search-hint"
            onInput={(e) => setQ((e.target as HTMLInputElement).value)}
          />
        </label>
        <p id="names-search-hint" class="small muted">
          {words.searchHint}
        </p>
        <label class="field">
          {words.sortLabel}
          <select value={sort} onChange={(e) => setSort((e.target as HTMLSelectElement).value as NameSort)}>
            <option value="name">{words.sortName}</option>
            <option value="newest">{words.sortNewest}</option>
          </select>
        </label>
        <label class="field">
          {words.perPageLabel}
          <select value={String(size)} onChange={(e) => setSize(Number((e.target as HTMLSelectElement).value))}>
            {NAMES_PAGE_SIZES.map((n) => (
              <option key={n} value={String(n)}>
                {words.perPageOption(n)}
              </option>
            ))}
          </select>
        </label>
        <fieldset class="names-types">
          <legend>{words.kindLabel}</legend>
          <div class="names-type-list">
            {kindList.map((k) => (
              <label class="choice names-type" key={k.key}>
                <input
                  type="checkbox"
                  checked={kinds.includes(k.key)}
                  onChange={() =>
                    setKinds((now) => (now.includes(k.key) ? now.filter((x) => x !== k.key) : [...now, k.key]))
                  }
                />{" "}
                {words.kindOption(k.label, k.count)}
              </label>
            ))}
            {/* Always there (and dimmed when nothing is ticked), so pressing it never drops the keyboard. */}
            {/* aria-disabled, not disabled: a disabled button drops the keyboard when it is pressed last. */}
            <button
              type="button"
              class="link-button small"
              aria-disabled={kinds.length === 0}
              onClick={() => kinds.length > 0 && setKinds([])}
            >
              {words.allKinds(everything)}
            </button>
          </div>
        </fieldset>
      </form>

      {smartOn && (
        <LazyPanel
          load={() => import("./NamesSmart")}
          what="the search by meaning"
          props={{
            api,
            campaignRef: ref,
            onResults: setSmart,
            showing: smart !== null,
            refreshKey: smartRefresh,
            leaveKey: smartLeave,
            count: smart?.length ?? 0,
            onSignedOut: () => setView("signed-out"),
          }}
        />
      )}

      {/* Always on the page, so a screen reader hears each new count and each result. */}
      <p class="small" role="status" aria-live="polite" aria-busy={busy}>
        {words.howMany(smart !== null ? smart.length : data.total)}
      </p>
      {/* Two boxes that are always there, each with one role for good: a role that changes is
          missed by screen readers. A result that repeats ("Saved." twice) is a new node, so it is
          heard again. */}
      <p class={news?.ok ? "ok" : "visually-hidden"} role="status" tabIndex={-1} ref={newsBox}>
        {news?.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>
      <p class={news && !news.ok ? "warn" : "visually-hidden"} role="alert">
        {news && !news.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>

      {undo && !undoing && (
        <p class="row">
          <button type="button" class="button secondary" onClick={() => void undoNamed()}>
            {undo.join ? notNamed.undoJoin : notNamed.undo}
          </button>
          <span class="small muted">{notNamed.undoHint}</span>
        </p>
      )}
      {undoing && (
        <WorkProgress
          stage={undoLooks === 0 ? (notNamed.stage["waiting"] as string) : (notNamed.stage["undoing"] as string)}
          startedAt={undoSeen.startedAt}
          updatedAt={undoSeen.movedAt}
          state={undoSeen.quiet ? "stalled" : "running"}
          progressUnknown
          whatToDo={notNamed.quiet}
        />
      )}

      {data.canEdit && picked.length >= 1 && (
        <section class="panel merge-bar" aria-label={words.selectedBar}>
          <p role="status">{picked.length === 1 ? words.selectedOne : words.selected(picked.length)}</p>
          <div class="row">
            {picked.length >= 2 && (
              <button type="button" class="button" ref={mergeOpener} disabled={merging || mergeBusy} onClick={openMerge}>
                {words.mergeSelected}
              </button>
            )}
            <button type="button" class="button secondary" disabled={mergeBusy} onClick={() => {
                setPicked([]);
                // The bar is gone: the keyboard goes to the first tick box.
                setTimeout(() => document.querySelector<HTMLInputElement>(".name-pick")?.focus(), 0);
              }}>
              {words.clearSelected}
            </button>
          </div>
          {merging && picked.length >= 2 && (
            <MergeMany
              ordered={ordered}
              kinds={data.kinds}
              keepId={keepId}
              onKeep={setKeepId}
              busy={mergeBusy}
              headingRef={mergeHeading}
              onYes={() => void mergeMany()}
              onNo={closeMerge}
            />
          )}
        </section>
      )}

      {shown.length === 0 ? (
        smart !== null ? null : <p class="muted">{q.trim() !== "" || kinds.length > 0 ? words.noMatch : words.noNames}</p>
      ) : (
        <ul class="names-list" aria-label={words.listLabel} aria-busy={busy}>
          {shown.map((row) => (
            <NameItem
              key={row.id}
              row={row}
              kinds={kindList}
              canEdit={data.canEdit}
              ticked={picked.some((r) => r.id === row.id)}
              onTick={() => pick(row)}
              open={editing === row.id}
              focusSaid={focusRow === row.id}
              onToggle={() => setEditing(editing === row.id ? null : row.id)}
              act={act}
              onNamed={(info) => named(row, info)}
              onSignedOut={() => setView("signed-out")}
              api={api}
              refId={ref}
              campaignPath={`/account/npc/${encodeURIComponent(ref)}/`}
            />
          ))}
        </ul>
      )}

      {smart === null && <PageBar
        id="names-page"
        label={words.pagesNav}
        moves={{ first: words.pageFirst, previous: words.pagePrevious, next: words.pageNext, last: words.pageLast }}
        page={data.page}
        pages={data.pages}
        busy={busy}
        notice={pageNotice}
        onGo={(wanted) => load(wanted)}
      />}
    </div>
  );
}

/** "A, B and C". */
function listOf(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** The small question under the bar: which name to keep, a warning when the kinds differ, and one
 * confirmation that names every name that goes (#2427). Escape cancels. */
function MergeMany({
  ordered,
  kinds,
  keepId,
  onKeep,
  busy,
  headingRef,
  onYes,
  onNo,
}: {
  ordered: NameRow[];
  kinds: NameKind[];
  keepId: string;
  onKeep: (id: string) => void;
  busy: boolean;
  headingRef: { current: HTMLHeadingElement | null };
  onYes: () => void;
  onNo: () => void;
}) {
  const keep = ordered.find((r) => r.id === keepId) ?? ordered[0];
  if (!keep) return null;
  const others = ordered.filter((r) => r.id !== keep.id);
  const kindKeys = [...new Set(ordered.map((r) => r.kind))];
  return (
    <div
      class="stack"
      role="dialog"
      aria-modal="false"
      aria-labelledby="merge-many-heading"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !busy) onNo();
      }}
    >
      <h3 id="merge-many-heading" tabIndex={-1} ref={headingRef}>
        {words.mergeManyHeading}
      </h3>
      <p class="small muted">{words.mergeManyLead}</p>
      {ordered.some((r) => r.mentions !== undefined) && (
        <p class="small muted">{words.mergeManyOrder}</p>
      )}
      <div role="radiogroup" aria-labelledby="merge-many-heading" class="stack">
        {ordered.map((r) => (
          <label class="choice" key={r.id}>
            <input
              type="radio"
              name="merge-keep"
              checked={r.id === keep.id}
              disabled={busy}
              onChange={() => onKeep(r.id)}
            />{" "}
            {r.name}
            {r.mentions !== undefined && <span class="muted small"> · {words.mentionsOf(r.mentions)}</span>}
          </label>
        ))}
      </div>
      {kindKeys.length > 1 && (
        <p class="note" role="note">
          {words.mergeManyKinds(listOf(kindKeys.map((k) => kindWords(kinds, k))), kindWords(kinds, keep.kind))}
        </p>
      )}
      <p>{words.mergeManyAsk(listOf(others.map((r) => r.name)), keep.name)}</p>
      <div class="row">
        <button type="button" class="button" aria-busy={busy} disabled={busy} onClick={onYes}>
          {busy ? words.mergeManyBusy : words.mergeManyYes}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onNo}>
          {words.mergeManyNo}
        </button>
      </div>
    </div>
  );
}

function kindWords(kinds: NameKind[], key: string): string {
  return kinds.find((k) => k.key === key)?.label ?? key;
}

interface ItemProps {
  row: NameRow;
  kinds: NameKind[];
  canEdit: boolean;
  /** Ticked for "Merge selected" (DMs only, #2427). */
  ticked: boolean;
  onTick: () => void;
  open: boolean;
  /** Put the cursor in the "how to say it" box when the editor opens (the link from Discord). */
  focusSaid?: boolean;
  onToggle: () => void;
  act: (row: NameRow, change: NameChange, ok: string) => Promise<string | null>;
  onNamed: (info: NamedInfo) => Promise<void>;
  onSignedOut: () => void;
  api: AccountApi;
  refId: string;
  campaignPath: string;
}

function NameItem({ row, kinds, canEdit, ticked, onTick, open, focusSaid, onToggle, act, onNamed, onSignedOut, api, refId, campaignPath }: ItemProps) {
  const panel = `name-edit-${row.id}`;
  return (
    <li class="name-row">
      <div class="name-head">
        {canEdit && !row.unnamed && (
          <label class="name-tick">
            <input
              type="checkbox"
              class="name-pick"
              checked={ticked}
              aria-label={words.pickLabel(row.name)}
              onChange={onTick}
            />
          </label>
        )}
        {canEdit ? (
          // The name itself opens the editor: a button that looks like a link, because it changes
          // the page rather than going to another one.
          <button
            type="button"
            class="link-button campaign-name name-open"
            aria-expanded={open}
            aria-controls={panel}
            aria-describedby="names-press-hint"
            onClick={onToggle}
          >
            {row.name}
          </button>
        ) : (
          <span class="campaign-name">{row.name}</span>
        )}
        {row.others.length > 0 && (
          <span class="small muted name-aka" title={row.others.join(", ")}>
            {words.alsoCalled}: {row.others.join(", ")}
          </span>
        )}
        <span class="tags">
          <span class="tag">{kindWords(kinds, row.kind)}</span>
          {row.secret && (
            <span class="tag">
              <span aria-hidden="true">{words.secretMark} </span>
              <span>{words.secretWords}</span>
            </span>
          )}
          {row.npcId && (
            <a class="small" href={`${campaignPath}${encodeURIComponent(row.npcId)}`} aria-label={words.npcLinkFor(row.name)}>
              {words.npcLink}
            </a>
          )}
        </span>
      </div>
      {canEdit && open && row.unnamed && (
        <NameThem id={panel} row={row} api={api} refId={refId} onDone={onNamed} onSignedOut={onSignedOut} />
      )}
      {canEdit && open && !row.unnamed && (
        <NameEditor id={panel} row={row} kinds={kinds} act={act} api={api} refId={refId} focusSaid={focusSaid === true} />
      )}
    </li>
  );
}

interface EditorProps {
  id: string;
  row: NameRow;
  kinds: NameKind[];
  act: ItemProps["act"];
  api: AccountApi;
  refId: string;
  focusSaid: boolean;
}

function NameEditor({ id, row, kinds, act, api, refId, focusSaid }: EditorProps) {
  const [spelling, setSpelling] = useState(row.name);
  const [kindKey, setKindKey] = useState(row.kind);
  const [other, setOther] = useState("");
  const [mergeQ, setMergeQ] = useState("");
  const [mergeFound, setMergeFound] = useState<NameRow[] | null>(null);
  // The search itself failed (not "nothing matches"), or has more matches than the first page.
  const [mergeProblem, setMergeProblem] = useState(false);
  const [mergeMore, setMergeMore] = useState(false);
  const [mergeInto, setMergeInto] = useState<NameRow | null>(null);
  const [ask, setAsk] = useState<"merge" | "remove" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  // How to say it (#2317): the box, what was last saved (so the button knows when nothing changed)
  // and the answer to the last save. Only a row the API gave a main name's id can have one.
  const [said, setSaid] = useState(row.saidAs ?? "");
  const [savedSaid, setSavedSaid] = useState(row.saidAs ?? "");
  const [saidNote, setSaidNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [savingSaid, setSavingSaid] = useState(false);
  const seq = useRef(0);
  // The two questions replace the buttons that opened them, so focus is moved by hand: onto the
  // question's first button when it opens, and back onto the opener on "No" (#2299).
  const mergeOpener = useRef<HTMLButtonElement>(null);
  const removeOpener = useRef<HTMLButtonElement>(null);
  const mergeYes = useRef<HTMLButtonElement>(null);
  const removeYes = useRef<HTMLButtonElement>(null);
  const lastAsk = useRef<"merge" | "remove" | null>(null);
  // Set by "No" only: typing in the search also closes a question, and must keep the cursor there.
  const giveBack = useRef(false);
  useEffect(() => {
    if (ask === "merge") mergeYes.current?.focus();
    else if (ask === "remove") removeYes.current?.focus();
    else if (giveBack.current && lastAsk.current === "merge") mergeOpener.current?.focus();
    else if (giveBack.current && lastAsk.current === "remove") removeOpener.current?.focus();
    giveBack.current = false;
    lastAsk.current = ask;
  }, [ask]);
  const decline = (): void => {
    giveBack.current = true;
    setAsk(null);
  };

  // The box follows the row when a read changes it (a rename, a merge), as the spelling box does.
  useEffect(() => {
    setSaid(row.saidAs ?? "");
    setSavedSaid(row.saidAs ?? "");
  }, [row.aliasId, row.saidAs]);
  const saidId = `said-as-${row.id}`;
  // Arriving from Discord's "Say it" link: the cursor goes to the box this link is for.
  useEffect(() => {
    if (focusSaid) document.getElementById(saidId)?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSaid]);
  const saveSaid = async (): Promise<void> => {
    if (!row.aliasId || !api.saveSaidAs || savingSaid) return;
    setSavingSaid(true);
    setSaidNote(null);
    try {
      const clean = said.trim();
      await api.saveSaidAs(refId, row.aliasId, clean === "" ? null : clean);
      setSaid(clean);
      setSavedSaid(clean);
      setSaidNote({ ok: true, text: clean === "" ? words.sayCleared : words.saySaved });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setSaidNote({ ok: false, text: kind === "bad-text" ? words.sayTooLong : (kind === "network" ? words.errors.network! : words.sayFailed) });
    } finally {
      setSavingSaid(false);
    }
  };

  // The box follows the name when a save changes it.
  useEffect(() => setSpelling(row.name), [row.name]);
  useEffect(() => setKindKey(row.kind), [row.kind]);

  // Finding the name to merge into: the same list, searched; this name itself is left out.
  useEffect(() => {
    const mine = ++seq.current;
    setMergeProblem(false);
    setMergeMore(false);
    if (mergeQ.trim() === "" || !api.allNames) {
      setMergeFound(null);
      return;
    }
    const timer = setTimeout(() => {
      api
        .allNames?.(refId, { q: mergeQ, kind: "", sort: "name", page: 1 })
        .then((read) => {
          if (mine !== seq.current) return;
          setMergeFound(read.names.filter((n) => n.id !== row.id));
          setMergeMore(read.pages > 1);
        })
        .catch(() => {
          if (mine !== seq.current) return;
          setMergeFound(null);
          setMergeProblem(true);
        });
    }, SEARCH_MS);
    return () => {
      clearTimeout(timer);
      seq.current++; // an answer still on its way is for words that are gone
    };
  }, [api, refId, mergeQ, row.id]);

  /** Resolves true when the change worked, so a box is emptied only then. */
  async function run(change: NameChange, ok: string): Promise<boolean> {
    setWorking(true);
    setProblem(null);
    const said = await act(row, change, ok);
    setWorking(false);
    if (said) setProblem(said);
    return !said;
  }

  return (
    <div class="name-edit" id={id}>
      <p role="alert" class={problem ? "warn small" : "visually-hidden"}>
        {problem ?? ""}
      </p>

      <form
        class="name-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (spelling.trim() !== row.name) void run({ action: "spell", name: spelling.trim() }, words.saved);
        }}
      >
        <label class="field">
          {words.spellLabel}
          <input type="text" value={spelling} maxLength={NAME_LIMIT} onInput={(e) => setSpelling((e.target as HTMLInputElement).value)} />
        </label>
        <button type="submit" class="button secondary" disabled={working || spelling.trim() === "" || spelling.trim() === row.name}>
          {words.saveSpelling}
        </button>
      </form>

      {row.aliasId && api.saveSaidAs && (
        <form
          class="name-form"
          onSubmit={(e) => {
            e.preventDefault();
            void saveSaid();
          }}
        >
          <label class="field" for={saidId}>
            {words.sayLabel(row.name)}
          </label>
          <input
            id={saidId}
            type="text"
            value={said}
            maxLength={80}
            aria-describedby={`${saidId}-hint`}
            disabled={savingSaid}
            onInput={(e) => {
              setSaid((e.target as HTMLInputElement).value);
              setSaidNote(null); // "Saved." must not stay over words that are not saved yet
            }}
          />
          <p class="small muted" id={`${saidId}-hint`}>
            {words.sayHint}
          </p>
          <SayIt
            api={api}
            campaignRef={refId}
            nameId={row.aliasId}
            name={row.name}
            fieldId={saidId}
            value={said}
            disabled={savingSaid}
            onValue={(text) => {
              setSaid(text);
              setSaidNote(null);
            }}
          />
          <button type="submit" class="button secondary" disabled={savingSaid || said.trim() === savedSaid}>
            {savingSaid ? words.saySaving : words.saySave}
          </button>
          <p
            class={saidNote ? (saidNote.ok ? "ok small" : "warn small") : "visually-hidden"}
            role={saidNote && !saidNote.ok ? "alert" : "status"}
          >
            {saidNote?.text ?? ""}
          </p>
        </form>
      )}

      <form
        class="name-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (kindKey !== row.kind) void run({ action: "kind", kind: kindKey }, words.saved);
        }}
      >
        <label class="field">
          {words.kindChange}
          <select value={kindKey} onChange={(e) => setKindKey((e.target as HTMLSelectElement).value)}>
            {kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" class="button secondary" disabled={working || kindKey === row.kind}>
          {words.saveKind}
        </button>
      </form>

      <label class="choice">
        <input
          type="checkbox"
          checked={row.hidden === true}
          disabled={working}
          onChange={(e) => void run({ action: "hide", hidden: (e.target as HTMLInputElement).checked }, words.saved)}
        />
        <span>{words.hideCheck}</span>
      </label>
      <p class="small muted">{words.hideHint}</p>
      <p role="status" class="small muted">
        {working ? words.waiting : ""}
      </p>

      <fieldset class="name-others">
        <legend>{words.othersHeading}</legend>
        {row.others.length === 0 ? (
          <p class="small muted">{words.noOthers}</p>
        ) : (
          <ul>
            {row.others.map((o) => (
              <li key={o} class="row">
                <span>{o}</span>
                <button
                  type="button"
                  class="button secondary"
                  aria-label={words.removeOther(o)}
                  disabled={working}
                  onClick={() => void run({ action: "remove-other", other: o }, words.saved)}
                >
                  {words.removeShort}
                </button>
              </li>
            ))}
          </ul>
        )}
        <form
          class="name-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (other.trim() === "") return;
            void run({ action: "add-other", other: other.trim() }, words.saved).then((worked) => worked && setOther(""));
          }}
        >
          <label class="field">
            {words.addOtherLabel}
            <input type="text" value={other} maxLength={NAME_LIMIT} onInput={(e) => setOther((e.target as HTMLInputElement).value)} />
          </label>
          <button type="submit" class="button secondary" disabled={working || other.trim() === ""}>
            {words.addOther}
          </button>
        </form>
      </fieldset>

      <fieldset class="name-merge">
        <legend>{words.mergeHeading}</legend>
        <p class="small muted">{words.mergeLead}</p>
        <label class="field">
          {words.mergeSearch}
          <input
            type="search"
            value={mergeQ}
            autoComplete="off"
            spellcheck={false}
            onInput={(e) => {
              setMergeQ((e.target as HTMLInputElement).value);
              setMergeInto(null);
              setAsk(null);
            }}
          />
        </label>
        {mergeProblem && (
          <p class="small warn" role="alert">
            {words.mergeSearchDown}
          </p>
        )}
        {mergeMore && (
          <p class="small muted">{words.mergeMore}</p>
        )}
        {mergeFound !== null &&
          (mergeFound.length === 0 ? (
            <p class="small muted" role="status">
              {words.mergeNone}
            </p>
          ) : (
            <fieldset class="name-merge-pick">
              <legend>{words.mergeChoose}</legend>
              {mergeFound.map((n) => (
                <label key={n.id} class="choice">
                  <input
                    type="radio"
                    name={`${id}-into`}
                    checked={mergeInto?.id === n.id}
                    onChange={() => {
                      setMergeInto(n);
                      setAsk(null);
                    }}
                  />
                  <span>
                    {n.name} <span class="muted small">({kindWords(kinds, n.kind)})</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ))}
        {ask === "merge" && mergeInto ? (
          <div class="row" role="group" aria-label={words.mergeAsk(row.name, mergeInto.name)}>
            <p>{words.mergeAsk(row.name, mergeInto.name)}</p>
            <button
              ref={mergeYes}
              type="button"
              class="button"
              disabled={working}
              onClick={() => void run({ action: "merge", into: mergeInto.id }, words.merged(row.name, mergeInto.name))}
            >
              {words.mergeYes}
            </button>
            <button type="button" class="button secondary" onClick={decline}>
              {words.mergeKeep}
            </button>
          </div>
        ) : (
          <button ref={mergeOpener} type="button" class="button secondary" disabled={working || !mergeInto} onClick={() => setAsk("merge")}>
            {words.mergeButton}
          </button>
        )}
      </fieldset>

      <fieldset class="name-remove">
        <legend>{words.removeHeading}</legend>
        {ask === "remove" ? (
          <div class="row" role="group" aria-label={words.removeAsk(row.name)}>
            <p>{words.removeAsk(row.name)}</p>
            <button
              ref={removeYes}
              type="button"
              class="button"
              disabled={working}
              onClick={() => void run({ action: "remove", confirm: true }, words.removed(row.name))}
            >
              {words.removeYes}
            </button>
            <button type="button" class="button secondary" onClick={decline}>
              {words.keep}
            </button>
          </div>
        ) : (
          <button ref={removeOpener} type="button" class="button secondary" disabled={working} onClick={() => setAsk("remove")}>
            {words.removeButton}
          </button>
        )}
      </fieldset>
    </div>
  );
}

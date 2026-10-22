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
import { NAME_LIMIT, namesPage as words } from "../content/names-words";
import {
  type AccountApi,
  ApiError,
  type NameChange,
  type NameKind,
  type NameRow,
  type NamesPage,
  type NameSort,
} from "./api";

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

export default function Names({ api, path }: { api: AccountApi; path: string }) {
  const ref = namesRefFromPath(path);
  const [view, setView] = useState<View>("loading");
  const [data, setData] = useState<NamesPage | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [sort, setSort] = useState<NameSort>("name");
  const [busy, setBusy] = useState(false);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [news, setNews] = useState<{ ok: boolean; text: string; n: number } | null>(null);
  // Only the newest read may set what is shown, so a slow old answer never overwrites a new one.
  const latest = useRef(0);
  const pageNow = useRef(1);
  const queryNow = useRef({ q, kind, sort });
  queryNow.current = { q, kind, sort };
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
      // An editor stays open only for a row still on the page: a search, filter or page change that
      // drops it must not bring it back, with old words in it, when the row returns (#2299).
      setEditing((open) => (open !== null && read.names.some((n) => n.id === open) ? open : null));
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
    const timer = setTimeout(() => void load(1), wait);
    return () => clearTimeout(timer);
    // `load` reads the latest query through a ref, so only these three restart the read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref, q, kind, sort]);

  /** One change to one name. Resolves with the refusal's words, or null when it worked. */
  async function act(row: NameRow, change: NameChange, ok: string): Promise<string | null> {
    if (!ref || !api.changeName) return words.errorFallback;
    try {
      await api.changeName(ref, row.id, change);
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      if (error instanceof ApiError && error.kind === "not-found") {
        // The name is gone (someone else removed it): the list is read again and says so.
        setEditing(null);
        setNews({ ok: false, text: said, n: ++sayings.current });
        // The editor is gone: the keyboard goes to the news, as after a remove or a merge.
        setTimeout(() => newsBox.current?.focus(), 0);
        await load(pageNow.current, true);
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
    await load(pageNow.current, true);
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
  const shown = data.names.filter((n) => data.canEdit || !n.secret);
  const everything = data.kinds.reduce((sum, k) => sum + k.count, 0);
  return (
    <div class="stack names-page">
      <section class="stack" aria-labelledby="names-campaign">
        <h2 id="names-campaign">{data.campaign}</h2>
        <p class="muted small">{words.lead}</p>
        <a class="button secondary" href={`/account/web/${encodeURIComponent(ref)}`}>
          {words.webLink}
        </a>
        {data.canEdit ? (
          <p class="small muted">{words.undoNote}</p>
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
          {words.kindLabel}
          <select value={kind} onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
            <option value="">{words.allKinds(everything)}</option>
            {data.kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {words.kindOption(k.label, k.count)}
              </option>
            ))}
          </select>
        </label>
        <label class="field">
          {words.sortLabel}
          <select value={sort} onChange={(e) => setSort((e.target as HTMLSelectElement).value as NameSort)}>
            <option value="name">{words.sortName}</option>
            <option value="newest">{words.sortNewest}</option>
          </select>
        </label>
      </form>

      {/* Always on the page, so a screen reader hears each new count and each result. */}
      <p class="small" role="status" aria-live="polite" aria-busy={busy}>
        {words.howMany(data.total)}
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

      {shown.length === 0 ? (
        <p class="muted">{q.trim() !== "" || kind !== "" ? words.noMatch : words.noNames}</p>
      ) : (
        <ul class="names-list" aria-label={words.listLabel} aria-busy={busy}>
          {shown.map((row) => (
            <NameItem
              key={row.id}
              row={row}
              kinds={data.kinds}
              canEdit={data.canEdit}
              open={editing === row.id}
              onToggle={() => setEditing(editing === row.id ? null : row.id)}
              act={act}
              api={api}
              refId={ref}
              campaignPath={`/campaigns/${encodeURIComponent(ref)}/npcs/`}
            />
          ))}
        </ul>
      )}

      <PageBar
        id="names-page"
        label={words.pagesNav}
        moves={{ first: words.pageFirst, previous: words.pagePrevious, next: words.pageNext, last: words.pageLast }}
        page={data.page}
        pages={data.pages}
        busy={busy}
        notice={pageNotice}
        onGo={(wanted) => load(wanted)}
      />
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
  open: boolean;
  onToggle: () => void;
  act: (row: NameRow, change: NameChange, ok: string) => Promise<string | null>;
  api: AccountApi;
  refId: string;
  campaignPath: string;
}

function NameItem({ row, kinds, canEdit, open, onToggle, act, api, refId, campaignPath }: ItemProps) {
  const panel = `name-edit-${row.id}`;
  return (
    <li class="name-row">
      <div class="name-head">
        <span class="campaign-name">{row.name}</span>
        <span class="tags">
          <span class="tag">{kindWords(kinds, row.kind)}</span>
          {row.secret && (
            <span class="tag">
              <span aria-hidden="true">{words.secretMark} </span>
              <span>{words.secretWords}</span>
            </span>
          )}
        </span>
      </div>
      {row.others.length > 0 && (
        <p class="small muted">
          {words.alsoCalled}: {row.others.join(", ")}
        </p>
      )}
      <div class="row">
        {row.npcId && (
          <a class="button secondary" href={`${campaignPath}${encodeURIComponent(row.npcId)}`} aria-label={words.npcLinkFor(row.name)}>
            {words.npcLink}
          </a>
        )}
        {canEdit && (
          <button
            type="button"
            class="button secondary"
            aria-expanded={open}
            aria-controls={panel}
            aria-label={open ? words.doneFor(row.name) : words.changeFor(row.name)}
            onClick={onToggle}
          >
            {open ? words.done : words.change}
          </button>
        )}
      </div>
      {canEdit && open && (
        <NameEditor id={panel} row={row} kinds={kinds} act={act} api={api} refId={refId} />
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
}

function NameEditor({ id, row, kinds, act, api, refId }: EditorProps) {
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
          checked={row.hidden ?? row.secret}
          disabled={working}
          onChange={(e) => void run({ action: "secret", secret: (e.target as HTMLInputElement).checked }, words.saved)}
        />
        <span>{words.secretCheck}</span>
      </label>

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

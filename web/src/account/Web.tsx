/** @jsxImportSource preact */
// The relationship web page (#2324): a campaign's names as a map, with the same names and connections
// as lists under it. Pick a name for the middle, choose how many steps out, tap a line to read its
// connections. A campaign's DMs can also change the words of a connection, make it secret, remove it or
// add one (the same actions as in Discord, so ↩️ Undo there still works). Who sees what is the API's
// rule: a server manager who isn't a DM gets no secrets and no editing, anyone else gets "not found".
// The page only shows what it is given. A web over BIG_WEB names asks before drawing, because a
// phone can draw it only slowly.
import { useEffect, useRef, useState } from "preact/hooks";

import { BIG_WEB, kindWords, webPage as words } from "../content/web-words";
import { type AccountApi, ApiError, type NameRow, type WebEdge, type WebGraph, type WebLinkChange } from "./api";
import WebMap from "./WebMap";
import { clampHops } from "./webLayout";

/** The campaign's address, read from `/account/web/<server>.<campaign>`. */
export function webRefFromPath(path: string): string | null {
  const match = /^\/account\/web\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null;
  }
}

type View = "loading" | "ok" | "none" | "down" | "signed-out";

const DEFAULT_HOPS = 2;

function refusal(error: unknown): string | null {
  const kind = error instanceof ApiError ? error.kind : "network";
  if (kind === "signed-out") return null;
  return words.errors[kind] ?? words.errorFallback;
}

export default function Web({ api, path }: { api: AccountApi; path: string }) {
  const ref = webRefFromPath(path);
  const [view, setView] = useState<View>("loading");
  const [graph, setGraph] = useState<WebGraph | null>(null);
  const [center, setCenter] = useState<string | null>(null);
  const [hops, setHops] = useState(DEFAULT_HOPS);
  // A web too big to draw without asking: what was asked for, held until the person says yes.
  const [big, setBig] = useState<{ names: number; center: string | null; hops: number } | null>(null);
  const [line, setLine] = useState<{ a: string; b: string } | null>(null);
  const [news, setNews] = useState<{ ok: boolean; text: string; n: number } | null>(null);
  const latest = useRef(0);
  const sayings = useRef(0);

  /** Reads the web around `at`. A big one waits for a yes unless `sure`. */
  async function load(at: string | null, steps: number, sure = false): Promise<void> {
    if (!ref || !api.webGraph) return;
    const mine = ++latest.current;
    try {
      if (!sure && api.webSize) {
        const size = await api.webSize(ref, { center: at, hops: steps });
        if (mine !== latest.current) return;
        if (size.names > BIG_WEB) {
          setBig({ names: size.names, center: at, hops: steps });
          setView((v) => (v === "loading" ? "ok" : v));
          return;
        }
      }
      const read = await api.webGraph(ref, { center: at, hops: steps });
      if (mine !== latest.current) return;
      setBig(null);
      setGraph(read);
      setCenter(read.center);
      setHops(clampHops(steps, read.maxHops));
      setLine((now) => (now && read.edges.some((e) => e.a === now.a && e.b === now.b) ? now : null));
      setView("ok");
    } catch (error) {
      if (mine !== latest.current) return;
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") setView("signed-out");
      else if (kind === "not-found" || kind === "not-allowed") setView("none");
      else setView("down");
    }
  }

  useEffect(() => {
    void load(null, DEFAULT_HOPS);
    // Only the campaign restarts the first read; later reads are the person's choices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref]);

  /** One change to the connections; the web is read again after it so the lines are the API's own. */
  async function act(change: WebLinkChange, ok: string): Promise<string | null> {
    if (!ref || !api.changeWebLink) return words.errorFallback;
    try {
      await api.changeWebLink(ref, change);
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      if (error instanceof ApiError && error.kind === "not-found") {
        setNews({ ok: false, text: said, n: ++sayings.current });
        await load(center, hops, true);
        return null;
      }
      return said;
    }
    setNews({ ok: true, text: ok, n: ++sayings.current });
    await load(center, hops, true);
    return null;
  }

  if (!ref || !api.webGraph) return <p role="status">{words.notYours}</p>;
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
        <button type="button" class="button secondary" onClick={() => void load(center, hops)}>
          {words.tryAgain}
        </button>
      </div>
    );
  if (view === "loading")
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  // The API never sends a secret to someone who isn't a DM; this keeps one from showing if it did.
  const canEdit = graph?.canEdit ?? false;
  const nodes = (graph?.nodes ?? []).filter((n) => canEdit || !n.secret);
  const visible = new Set(nodes.map((n) => n.id));
  const edges = (graph?.edges ?? [])
    .filter((e) => visible.has(e.a) && visible.has(e.b))
    .map((e) => (canEdit ? e : { ...e, links: e.links.filter((l) => !l.secret) }))
    .filter((e) => e.links.length > 0);
  const nameOf = (id: string): string => nodes.find((n) => n.id === id)?.name ?? id;
  const maxHops = graph?.maxHops ?? 1;
  const chosen = line ? (edges.find((e) => e.a === line.a && e.b === line.b || e.a === line.b && e.b === line.a) ?? null) : null;

  return (
    <div class="stack web-page">
      <p class="muted small">{words.lead}</p>
      {canEdit ? (
        <p class="small muted">{words.undoNote}</p>
      ) : (
        <p class="note" role="note">
          {words.readOnly}
        </p>
      )}

      <div class="web-tools-form">
        <CentrePicker api={api} refId={ref} current={center ? nameOf(center) : ""} onPick={(id) => void load(id, hops)} />
        <label class="field">
          {words.hopsLabel}
          <select
            value={String(hops)}
            aria-describedby="web-hops-hint"
            onChange={(e) => void load(center, Number((e.target as HTMLSelectElement).value))}
          >
            {Array.from({ length: maxHops }, (_, i) => i + 1).map((n) => (
              <option key={n} value={String(n)}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <p id="web-hops-hint" class="small muted">
          {words.hopsHint(maxHops)}
        </p>
      </div>

      {big !== null && (
        <div class="panel stack" role="alert">
          <p>{words.bigAsk(big.names)}</p>
          <div class="row">
            <button type="button" class="button" onClick={() => void load(big.center, big.hops, true)}>
              {words.bigYes}
            </button>
            <button type="button" class="button secondary" onClick={() => setBig(null)}>
              {words.bigNo}
            </button>
          </div>
        </div>
      )}

      <p class="small" role="status" aria-live="polite">
        {words.howMany(nodes.length, edges.length)}
      </p>
      {graph?.truncated && <p class="note">{words.truncated}</p>}
      <p class={news?.ok ? "ok" : "visually-hidden"} role="status">
        {news?.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>
      <p class={news && !news.ok ? "warn" : "visually-hidden"} role="alert">
        {news && !news.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>

      {nodes.length <= 1 && edges.length === 0 ? (
        <p class="muted">{words.empty}</p>
      ) : (
        center && (
          <WebMap
            nodes={nodes}
            edges={edges}
            center={center}
            selected={line}
            onCenter={(id) => void load(id, hops)}
            onLine={(a, b) => setLine({ a, b })}
          />
        )
      )}

      {chosen && (
        <LinePanel
          key={`${chosen.a}|${chosen.b}`}
          edge={chosen}
          nameOf={nameOf}
          canEdit={canEdit}
          act={act}
          onClose={() => setLine(null)}
        />
      )}

      <section class="stack" aria-labelledby="web-list">
        <h2 id="web-list">{words.listHeading}</h2>
        <h3 class="small">{words.namesHeading}</h3>
        <ul class="web-names">
          {nodes.map((n) => (
            <li key={n.id} class="name-row">
              <span class="campaign-name">
                {n.secret && <span aria-hidden="true">{words.secretMark} </span>}
                {n.name}
              </span>{" "}
              <span class="tag">{kindWords(n.kind)}</span>
              {n.id === center ? (
                <span class="small muted"> {words.inMiddle}</span>
              ) : (
                <button type="button" class="button secondary" onClick={() => void load(n.id, hops)} aria-label={words.makeMiddle(n.name)}>
                  {words.makeMiddleShort}
                </button>
              )}
            </li>
          ))}
        </ul>
        <h3 class="small">{words.linesHeading}</h3>
        <ul class="web-lines">
          {edges.map((e) => (
            <li key={`${e.a}|${e.b}`} class="name-row">
              {words.lineText(nameOf(e.a), nameOf(e.b), e.label, e.more)}{" "}
              <button
                type="button"
                class="button secondary"
                onClick={() => setLine({ a: e.a, b: e.b })}
                aria-label={words.seeLine(nameOf(e.a), nameOf(e.b))}
              >
                {words.seeLineShort}
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Find a name by typing; choosing one puts it in the middle. */
function CentrePicker({ api, refId, current, onPick }: { api: AccountApi; refId: string; current: string; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<NameRow[] | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    if (q.trim() === "" || !api.allNames) {
      setFound(null);
      return;
    }
    const mine = ++latest.current;
    const timer = setTimeout(() => {
      void api
        .allNames?.(refId, { q, kind: "", sort: "name", page: 1 })
        .then((page) => {
          if (mine === latest.current) setFound(page.names.slice(0, 8));
        })
        .catch(() => {
          if (mine === latest.current) setFound([]);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [api, refId, q]);

  return (
    <div class="field">
      <label>
        {words.centreSearch}
        <input
          type="search"
          value={q}
          autoComplete="off"
          spellcheck={false}
          aria-describedby="web-centre-hint"
          onInput={(e) => setQ((e.target as HTMLInputElement).value)}
        />
      </label>
      <p id="web-centre-hint" class="small muted">
        {current ? words.centreNow(current) : words.centreHint}
      </p>
      {found !== null && (
        <ul class="web-found" aria-label={words.centreLabel}>
          {found.length === 0 && <li class="muted">{words.centreNone}</li>}
          {found.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                class="button secondary"
                onClick={() => {
                  setQ("");
                  setFound(null);
                  onPick(n.id);
                }}
              >
                {n.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** All the connections between two names, and (for a DM) the controls to change them. */
function LinePanel({
  edge,
  nameOf,
  canEdit,
  act,
  onClose,
}: {
  edge: WebEdge;
  nameOf: (id: string) => string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
  onClose: () => void;
}) {
  const a = nameOf(edge.a);
  const b = nameOf(edge.b);
  const box = useRef<HTMLElement>(null);
  useEffect(() => box.current?.focus(), []);
  return (
    <section class="panel stack" aria-labelledby="web-line-h" tabIndex={-1} ref={box}>
      <h2 id="web-line-h">{words.edgePanel(a, b)}</h2>
      <ol class="stack">
        {edge.links.map((l, i) => (
          <LinkRow key={l.id} n={i + 1} link={l} a={a} b={b} canEdit={canEdit} act={act} />
        ))}
      </ol>
      {canEdit && <AddLink edge={edge} act={act} />}
      <button type="button" class="button secondary" onClick={onClose}>
        {words.close}
      </button>
    </section>
  );
}

function LinkRow({
  n,
  link,
  a,
  b,
  canEdit,
  act,
}: {
  n: number;
  link: WebEdge["links"][number];
  a: string;
  b: string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
}) {
  const [text, setText] = useState(link.words);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const run = async (change: WebLinkChange, ok: string): Promise<void> => setProblem(await act(change, ok));
  return (
    <li class="stack">
      <p>
        {link.secret && <span aria-hidden="true">{words.secretMark} </span>}
        {words.linkText(a, b, link.words, link.direction)}
        {link.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
      </p>
      {canEdit && (
        <div class="stack">
          <label class="field">
            {words.wordsLabel(n)}
            <input type="text" value={text} maxLength={100} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
          </label>
          <div class="row">
            <button
              type="button"
              class="button secondary"
              aria-label={words.saveWords(n)}
              onClick={() => void run({ action: "words", id: link.id, words: text }, words.saved)}
            >
              {words.save}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => void run({ action: "secret", id: link.id, secret: !link.secret }, words.saved)}
            >
              {link.secret ? words.makeKnown(n) : words.makeSecret(n)}
            </button>
            {!asking && (
              <button type="button" class="button secondary" onClick={() => setAsking(true)}>
                {words.removeLink(n)}
              </button>
            )}
          </div>
          {asking && (
            <div class="row" role="group" aria-label={words.removeAsk}>
              <span>{words.removeAsk}</span>
              <button type="button" class="button" onClick={() => void run({ action: "remove", id: link.id, confirm: true }, words.removed)}>
                {words.removeYes}
              </button>
              <button type="button" class="button secondary" onClick={() => setAsking(false)}>
                {words.keep}
              </button>
            </div>
          )}
          {problem && (
            <p class="warn" role="alert">
              {problem}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function AddLink({ edge, act }: { edge: WebEdge; act: (change: WebLinkChange, ok: string) => Promise<string | null> }) {
  const [text, setText] = useState("");
  const [secret, setSecret] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      class="stack"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        const said = await act({ action: "add", a: edge.a, b: edge.b, words: text, secret }, words.added);
        setProblem(said);
        if (said === null) setText("");
      }}
    >
      <h3 class="small">{words.addHeading}</h3>
      <label class="field">
        {words.addWords}
        <input type="text" value={text} maxLength={100} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
      </label>
      <label class="check">
        <input type="checkbox" checked={secret} onChange={(e) => setSecret((e.target as HTMLInputElement).checked)} /> {words.addSecret}
      </label>
      <button type="submit" class="button">
        {words.addButton}
      </button>
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </form>
  );
}

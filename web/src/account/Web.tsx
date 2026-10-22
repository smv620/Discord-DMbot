/** @jsxImportSource preact */
// The relationship web page (#2324): a campaign's names as a map, with the same names and connections
// as lists under it. Pick a name for the middle, choose how many steps out, tap a line to read its
// connections. A campaign's DMs can also change the words of a connection, make it secret, remove it or
// add one (the same actions as in Discord, so ↩️ Undo there still works). Who sees what is the API's
// rule: a server manager who isn't a DM gets no secrets and no editing, anyone else gets "not found".
// The page only shows what it is given. A web over BIG_WEB names asks before drawing, because a
// phone can draw it only slowly.
import { useEffect, useRef, useState } from "preact/hooks";

import { NAME_LIMIT } from "../content/names-words";
import { BIG_WEB, kindWords, webPage as words } from "../content/web-words";
import {
  type AccountApi,
  ApiError,
  type NameChange,
  type NameRow,
  type WebEdge,
  type WebGraph,
  type WebLinkChange,
  type WebNode,
  type WebRevealTarget,
  type WebView,
} from "./api";
import WebMap from "./WebMap";
import { clampHops, isTheory } from "./webLayout";
import BackToCampaign from "./BackToCampaign";

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
  // What is typed in the steps box: it may be half-typed, so it is kept apart from the steps in use.
  const [hopsText, setHopsText] = useState(String(DEFAULT_HOPS));
  const [editing, setEditing] = useState<string | null>(null);
  // A web too big to draw without asking: what was asked for, held until the person says yes.
  const [big, setBig] = useState<{ names: number; center: string | null; hops: number } | null>(null);
  const [line, setLine] = useState<{ a: string; b: string } | null>(null);
  const [news, setNews] = useState<{ ok: boolean; text: string; n: number } | null>(null);
  const latest = useRef(0);
  const sayings = useRef(0);
  // Which web is asked for (#2405): a DM's "players" or "full"; a player who peeks, "full"; else the
  // API's own pick. Kept in a ref too, so a read started before a switch is not mixed up with it.
  const [asked, setAsked] = useState<WebView | undefined>(undefined);
  const askedNow = useRef<WebView | undefined>(undefined);
  // Once a response says the person is a DM they stay one for this visit, even in the players' view.
  const [isDm, setIsDm] = useState(false);
  const [meta, setMeta] = useState<{ visibility?: WebGraph["visibility"] | undefined; canPeek?: boolean | undefined }>({});
  const [asking, setAsking] = useState(false);
  const peekSwitch = useRef<HTMLInputElement>(null);

  /** Reads the web around `at`. A big one waits for a yes unless `sure`. */
  async function load(at: string | null, steps: number, sure = false): Promise<void> {
    if (!ref || !api.webGraph) return;
    const mine = ++latest.current;
    const view = askedNow.current;
    setBig(null);
    try {
      if (!sure && api.webSize) {
        const size = await api.webSize(ref, { center: at, hops: steps, ...(view ? { view } : {}) });
        if (mine !== latest.current) return;
        if (size.names > BIG_WEB) {
          setBig({ names: size.names, center: at, hops: steps });
          return;
        }
      }
      const read = await api.webGraph(ref, { center: at, hops: steps, ...(view ? { view } : {}) });
      if (mine !== latest.current) return;
      setGraph(read);
      if (read.canEdit) setIsDm(true);
      setMeta((now) => ({
        visibility: read.visibility ?? now.visibility,
        canPeek: read.canPeek ?? now.canPeek,
      }));
      setCenter(read.center);
      setHops(clampHops(steps, read.maxHops));
      setHopsText(String(clampHops(steps, read.maxHops)));
      setLine((now) => (now && read.edges.some((e) => (e.a === now.a && e.b === now.b) || (e.a === now.b && e.b === now.a)) ? now : null));
      setView("ok");
    } catch (error) {
      if (mine !== latest.current) return;
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "peek-off") {
        // The DM turned peeking off while it was on: back to the normal view, with the reason.
        switchTo(undefined);
        setMeta((now) => ({ ...now, visibility: "private", canPeek: false }));
        setNews({ ok: false, text: words.errors["peek-off"]!, n: ++sayings.current });
        await load(at, steps, true);
        return;
      }
      if (kind === "signed-out") setView("signed-out");
      else if (kind === "not-found" || kind === "not-allowed") setView("none");
      else setView("down");
    }
  }

  /** Changes which web is asked for, and reads it (#2405). */
  function switchTo(next: WebView | undefined): void {
    askedNow.current = next;
    setAsked(next);
  }
  function chooseView(next: WebView | undefined): void {
    switchTo(next);
    setLine(null);
    void load(center, hops, true);
  }

  /** A player agreed to the warning: tell the API they peek, then read the full web. */
  async function peek(): Promise<void> {
    setAsking(false);
    if (!ref || !api.webPeek) return;
    try {
      await api.webPeek(ref);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return setView("signed-out");
      // Only the API saying the DM turned peeking off may lock the switch; a network or server
      // failure says so in its own words and leaves the switch usable to try again.
      if (error instanceof ApiError && error.kind === "peek-off") {
        setMeta((now) => ({ ...now, visibility: "private", canPeek: false }));
      }
      setNews({ ok: false, text: refusal(error) ?? words.errorFallback, n: ++sayings.current });
      return;
    }
    chooseView("full");
    peekSwitch.current?.focus();
  }

  /** A DM shows a name or a connection to the players, then the web is read again. */
  async function reveal(target: WebRevealTarget): Promise<string | null> {
    if (!ref || !api.webReveal) return words.errorFallback;
    try {
      await api.webReveal(ref, target);
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      return said;
    }
    setNews({ ok: true, text: words.revealed, n: ++sayings.current });
    await load(center, hops, true);
    return null;
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

  /** One change to a name (spelling or kind): the same route the Names page uses, then the web is read again. */
  async function actName(id: string, change: NameChange, ok: string): Promise<string | null> {
    if (!ref || !api.changeName) return words.errorFallback;
    try {
      await api.changeName(ref, id, change);
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
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
  const askBox = big !== null && (
        <div class="panel stack" role="alert">
          <p>{words.bigAsk(big.names)}</p>
          <div class="row">
            <button type="button" class="button" onClick={() => void load(big.center, big.hops, true)}>
              {words.bigYes}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                // Declining the first draw must not leave a blank page: read the nearest names only.
                if (!graph) void load(big.center, 1, true);
                else setBig(null);
              }}
            >
              {words.bigNo}
            </button>
          </div>
        </div>
      );

  if (view === "loading" && big !== null)
    return <div class="stack web-page">{askBox}</div>;
  if (view === "loading")
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  if (!graph)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  // The API never sends a secret to someone it isn't for; this keeps one from showing if it did.
  // Secrets are for a DM in Full View and for a player who is peeking, never in the players' view.
  const showAll = asked === "players" ? false : graph.canEdit || asked === "full";
  // Changing anything is for a DM, and not in the players' view (read-only, so what they see is checked).
  const canEdit = graph.canEdit && asked !== "players";
  const peeking = !isDm && asked === "full";
  // Nor what the players haven't been told yet: the page may still hold the full web for a moment
  // after a switch to the players' view, and this keeps it off the screen.
  const nodes = graph.nodes.filter((n) => showAll || (!n.secret && n.revealed !== false));
  const visible = new Set(nodes.map((n) => n.id));
  const edges = graph.edges
    .filter((e) => visible.has(e.a) && visible.has(e.b))
    // For anyone but a DM the label is rebuilt from the known connections, so a secret one's words can
    // never ride along on the line even if the API sent them.
    .map((e) => {
      // A pin is the DM's alone: dropped for a player who peeks, and in the players' view.
      if (showAll && canEdit) return e;
      if (showAll) return { ...e, links: e.links.map(({ pinned: _pinned, ...l }) => l) };
      const links = e.links
        .filter((l) => !l.secret && l.revealed !== false)
        .map(({ belief, pinned: _pinned, ...l }) => (belief ? { ...l, belief: { confidence: belief.confidence, status: belief.status } } : l));
      return { ...e, links, label: links.at(-1)?.words ?? "", more: Math.max(0, links.length - 1) };
    })
    .filter((e) => e.links.length > 0);
  const nameOf = (id: string): string => nodes.find((n) => n.id === id)?.name ?? id;
  const maxHops = graph.maxHops;
  const chosen = line ? (edges.find((e) => e.a === line.a && e.b === line.b || e.a === line.b && e.b === line.a) ?? null) : null;

  return (
    <div class="stack web-page">
      {ref && <BackToCampaign campaignRef={ref} />}
      <p class="muted small">{words.lead}</p>
      {canEdit ? (
        <p class="small muted">{words.undoNote}</p>
      ) : (
        <p class="note" role="note">
          {words.readOnly}
        </p>
      )}

      {isDm && (
        <fieldset class="web-view">
          <legend>{words.viewLegend}</legend>
          <label class="choice">
            <input type="radio" name="web-view" checked={asked !== "players"} onChange={() => chooseView("full")} />
            <span>{words.fullView}</span>
          </label>
          <label class="choice">
            <input type="radio" name="web-view" checked={asked === "players"} onChange={() => chooseView("players")} />
            <span>{words.playersView}</span>
          </label>
          <p class="small muted">{asked === "players" ? words.playersViewNote : words.fullViewNote}</p>
        </fieldset>
      )}
      {!isDm && api.webPeek && (
        <PeekSwitch
          on={asked === "full"}
          disabled={meta.canPeek === false || meta.visibility === "private"}
          inputRef={peekSwitch}
          onTurnOn={() => setAsking(true)}
          onTurnOff={() => chooseView(undefined)}
        />
      )}
      {asking && (
        <PeekAsk
          onYes={() => void peek()}
          onCancel={() => {
            setAsking(false);
            peekSwitch.current?.focus();
          }}
        />
      )}
      {peeking && <p class="note" role="note">{words.peeking}</p>}

      <div class="web-tools-form">
        <CentrePicker api={api} refId={ref} current={center ? nameOf(center) : ""} onPick={(id) => void load(id, hops)} />
        <label class="field">
          {words.hopsLabel}
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={maxHops}
            value={hopsText}
            aria-describedby="web-hops-hint"
            onInput={(e) => {
              const text = (e.target as HTMLInputElement).value;
              setHopsText(text);
              const n = Number(text);
              if (text.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= maxHops) void load(center, n);
            }}
            onBlur={() => setHopsText(String(hops))}
          />
        </label>
        <p id="web-hops-hint" class="small muted">
          {words.hopsHint(maxHops)}
        </p>
      </div>

      {askBox}

      <p class="small" role="status" aria-live="polite">
        {words.howMany(nodes.length, edges.length)}
      </p>
      {graph.truncated && <p class="note">{words.truncated}</p>}
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
          <>
            <a class="small" href="#web-list">
              {words.skipMap}
            </a>
            <WebMap
              nodes={nodes}
              edges={edges}
              center={center}
              selected={line}
              onCenter={(id) => void load(id, hops)}
              onLine={(a, b) => setLine({ a, b })}
            />
          </>
        )
      )}

      {chosen && (
        <LinePanel
          key={`${chosen.a}|${chosen.b}`}
          edge={chosen}
          nameOf={nameOf}
          canEdit={canEdit}
          act={act}
          reveal={reveal}
          onClose={() => setLine(null)}
        />
      )}

      <section class="stack" aria-labelledby="web-list">
        <h2 id="web-list">{words.listHeading}</h2>
        <h3 class="small">{words.namesHeading}</h3>
        <ul class="web-names">
          {nodes.map((n) => (
            <li key={n.id} class={n.revealed === false ? "name-row web-dim" : "name-row"}>
              <span class="campaign-name">
                {n.secret && <span aria-hidden="true">{words.secretMark} </span>}
                {n.name}
              </span>
              {n.secret && <span class="visually-hidden"> ({words.secretWords})</span>}{" "}
              {n.revealed === false && <span class="small muted">({words.notRevealed}) </span>}
              <span class="tag">{kindWords(n.kind)}</span>
              {n.id === center ? (
                <span class="small muted"> {words.inMiddle}</span>
              ) : (
                <button type="button" class="button secondary" onClick={() => void load(n.id, hops)} aria-label={words.makeMiddle(n.name)}>
                  {words.makeMiddleShort}
                </button>
              )}
              {canEdit && n.revealed === false && api.webReveal && (
                <button
                  type="button"
                  class="button secondary"
                  aria-label={words.revealFor(n.name)}
                  onClick={() => void reveal({ kind: "name", id: n.id })}
                >
                  {words.reveal}
                </button>
              )}
              {canEdit && api.changeName && (
                <button
                  type="button"
                  class="button secondary"
                  aria-expanded={editing === n.id}
                  aria-label={words.changeFor(n.name)}
                  onClick={() => setEditing(editing === n.id ? null : n.id)}
                >
                  {editing === n.id ? words.doneShort : words.changeShort}
                </button>
              )}
              {canEdit && editing === n.id && (
                <NodePanel node={n} act={actName} />
              )}
            </li>
          ))}
        </ul>
        <h3 class="small">{words.linesHeading}</h3>
        <ul class="web-lines">
          {edges.map((e) => (
            <li key={`${e.a}|${e.b}`} class="name-row">
              {isTheory(e) && <span aria-hidden="true">{words.theoryMark} </span>}
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
  reveal,
  onClose,
}: {
  edge: WebEdge;
  nameOf: (id: string) => string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
  reveal: (target: WebRevealTarget) => Promise<string | null>;
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
          <LinkRow key={l.id} n={i + 1} link={l} a={a} b={b} canEdit={canEdit} act={act} reveal={reveal} />
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
  reveal,
}: {
  n: number;
  link: WebEdge["links"][number];
  a: string;
  b: string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
  reveal: (target: WebRevealTarget) => Promise<string | null>;
}) {
  const [text, setText] = useState(link.words);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const run = async (change: WebLinkChange, ok: string): Promise<void> => setProblem(await act(change, ok));
  return (
    <li class={link.revealed === false ? "stack web-dim" : "stack"}>
      <p>
        {link.secret && <span aria-hidden="true">{words.secretMark} </span>}
        {link.belief && <span aria-hidden="true">{words.theoryMark} </span>}
        {words.linkText(a, b, link.words, link.direction)}
        {link.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
      </p>
      {canEdit && link.pinned && <p class="small">{words.pinMark} {words.pinnedWords}</p>}
      {link.belief && (
        <p class="small">
          {link.belief.status} · {words.confidence(Math.round(link.belief.confidence * 100))}
        </p>
      )}
      {link.belief?.contradicted && <p class="small warn">{words.believesFalse}</p>}
      {link.revealed === false && <p class="small muted">{words.notRevealedFull}</p>}
      {canEdit && link.revealed === false && (
        <div class="row">
          <button
            type="button"
            class="button secondary"
            aria-label={words.revealLink(n)}
            onClick={() => void reveal({ kind: "link", id: link.id }).then(setProblem)}
          >
            {words.reveal}
          </button>
        </div>
      )}
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
              aria-label={link.secret ? words.makeKnown(n) : words.makeSecret(n)}
              onClick={() => void run({ action: "secret", id: link.id, secret: !link.secret }, words.saved)}
            >
              {link.secret ? words.makeKnownShort : words.makeSecretShort}
            </button>
            <button
              type="button"
              class="button secondary"
              aria-label={link.pinned ? words.unpinLabel(n) : words.pinLabel(n)}
              onClick={() =>
                void run(
                  { action: "pin", id: link.id, pinned: !link.pinned },
                  link.pinned ? words.unpinnedDone : words.pinnedDone,
                )
              }
            >
              {link.pinned ? words.unpin : words.pin}
            </button>
            {!asking && (
              <button type="button" class="button secondary" aria-label={words.removeLink(n)} onClick={() => setAsking(true)}>
                {words.removeShort}
              </button>
            )}
          </div>
          <p class="small muted">{words.pinHint}</p>
          {asking && (
            <div class="row" role="group" aria-label={words.removeShort}>
              <span>{words.removeAsk(a, b)}</span>
              <button type="button" class="button" autoFocus onClick={() => void run({ action: "remove", id: link.id, confirm: true }, words.removed)}>
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
        if (said === null) {
          setText("");
          setSecret(false);
        }
      }}
    >
      <h3 class="small">{words.addHeading}</h3>
      <label class="field">
        {words.addWords}
        <input type="text" value={text} maxLength={100} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
      </label>
      <label class="choice">
        <input type="checkbox" checked={secret} onChange={(e) => setSecret((e.target as HTMLInputElement).checked)} />
        <span>{words.addSecret}</span>
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

/** A name's spelling and kind, for a DM. Both go through the same route as the Names page. */
function NodePanel({ node, act }: { node: WebNode; act: (id: string, change: NameChange, ok: string) => Promise<string | null> }) {
  const [spelling, setSpelling] = useState(node.name);
  const [kind, setKind] = useState(node.kind);
  const [problem, setProblem] = useState<string | null>(null);
  const kinds = Object.keys(words.kinds).includes(node.kind) ? Object.keys(words.kinds) : [node.kind, ...Object.keys(words.kinds)];
  return (
    <div class="stack" role="group" aria-label={words.nodePanel(node.name)}>
      <label class="field">
        {words.spellLabel}
        <input type="text" value={spelling} maxLength={NAME_LIMIT} onInput={(e) => setSpelling((e.target as HTMLInputElement).value)} />
      </label>
      <button
        type="button"
        class="button secondary"
        onClick={async () => setProblem(await act(node.id, { action: "spell", name: spelling }, words.saved))}
      >
        {words.saveSpelling}
      </button>
      <label class="field">
        {words.kindChange}
        <select value={kind} onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {kindWords(k)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        class="button secondary"
        onClick={async () => setProblem(await act(node.id, { action: "kind", kind }, words.saved))}
      >
        {words.saveKind}
      </button>
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}


/** The player's "Peek behind the DM screen" switch. Greyed out, with the reason, when the DM turned
 * peeking off; it stays focusable so a screen reader can read why. */
function PeekSwitch({
  on,
  disabled,
  inputRef,
  onTurnOn,
  onTurnOff,
}: {
  on: boolean;
  disabled: boolean;
  inputRef: { current: HTMLInputElement | null };
  onTurnOn: () => void;
  onTurnOff: () => void;
}) {
  return (
    <div class="web-peek">
      <label class={disabled ? "choice web-peek-off" : "choice"}>
        <input
          type="checkbox"
          role="switch"
          ref={inputRef}
          checked={on && !disabled}
          aria-disabled={disabled ? "true" : undefined}
          aria-describedby={disabled ? "web-peek-note" : undefined}
          onClick={(e) => {
            if (disabled) {
              e.preventDefault();
              return;
            }
            // The box flips by itself on a click; what it shows is decided by the page.
            e.preventDefault();
            if (on) onTurnOff();
            else onTurnOn();
          }}
        />
        <span>{words.peekSwitch}</span>
      </label>
      {!on && <p class="small muted">{words.normalView}</p>}
      {disabled && (
        <p id="web-peek-note" class="small">
          {words.peekOffNote}
        </p>
      )}
    </div>
  );
}

/** The warning before a player peeks (#2405). Cancel changes nothing. */
function PeekAsk({ onYes, onCancel }: { onYes: () => void; onCancel: () => void }) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div
      class="panel stack"
      role="alertdialog"
      aria-labelledby="web-peek-title"
      aria-describedby="web-peek-warn"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <h2 id="web-peek-title">{words.peekTitle}</h2>
      <p id="web-peek-warn">{words.peekWarn}</p>
      <div class="row">
        <button type="button" class="button" onClick={onYes}>
          {words.peekYes}
        </button>
        <button type="button" class="button secondary" ref={cancel} onClick={onCancel}>
          {words.peekCancel}
        </button>
      </div>
    </div>
  );
}

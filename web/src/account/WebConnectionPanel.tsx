/** @jsxImportSource preact */
// The Story Graph's redesigned connection panel (#4155, owner request 2026-11-11): "+ Add a connection" on top, then one compact line
// for every connection between the two names (🤫 when secret, the direction as an arrow, the words); a line opens to its full options,
// one at a time. A DM's edits to words, secret and pin are staged: nothing is sent until Save, a dot shows on the line and on the
// heading while something waits, and leaving with changes pending asks first (closing, opening another line, picking another
// name or line on the map, and leaving the page). A player gets the same list, read-only. Names are only ever text on the page.
// Off (CONNECTION_PANEL_LIVE) the old panel, WebLinePanel, is shown as it was. Its own file, loaded when a line is chosen.
import { useEffect, useRef, useState } from "preact/hooks";

import { KNOWS_FEELING_LIVE } from "../content/knows-feeling-switch";
import { feelingOf } from "../content/knows-feeling-words";
import { KNOWS_HOW_LIVE } from "../content/knows-how-switch";
import { knowsHowOf, knowsHowWords } from "../content/knows-how-words";
import { panelWords as p } from "../content/connection-panel-words";
import { WHO_KNOWS_LIVE } from "../content/who-knows-switch";
import { webPage as words } from "../content/web-words";
import { type AccountApi, type WebEdge, type WebLinkChange, type WebRevealTarget } from "./api";
import LazyPanel from "./LazyPanel";
import SavingWait from "./SavingWait";
import { dimmedName, markSecret } from "./know";
import { WhoKnows } from "./WebLinePanel";

type Link = WebEdge["links"][number];
/** What the DM has picked on one connection and not yet saved. */
export interface Staged {
  words?: string;
  secret?: boolean;
  pinned?: boolean;
}
/** Asked by the page before it leaves this panel: true when the panel has taken over (it is asking, and will run `leave` if the DM says Leave). */
export type Guard = (leave: () => void) => boolean;

/** The changes a staged pick makes to a connection: only what differs from what it is now. */
export function changesOf(link: Link, staged: Staged | undefined): WebLinkChange[] {
  if (!staged) return [];
  const out: WebLinkChange[] = [];
  if (staged.words !== undefined && staged.words.trim() !== (link.rawWords ?? link.words).trim()) out.push({ action: "words", id: link.id, words: staged.words });
  if (staged.secret !== undefined && staged.secret !== link.secret) out.push({ action: "secret", id: link.id, secret: staged.secret });
  if (staged.pinned !== undefined && staged.pinned !== (link.pinned ?? false)) out.push({ action: "pin", id: link.id, pinned: staged.pinned });
  return out;
}

export default function ConnectionPanel({
  edge,
  nameOf,
  kindOf,
  canEdit,
  act,
  reveal,
  revealing,
  api,
  refId,
  onClose,
  guard,
  open: firstOpen = null,
}: {
  edge: WebEdge;
  nameOf: (id: string) => string;
  kindOf: (id: string) => string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string, own?: Partial<Record<string, string>>) => Promise<string | null>;
  reveal: (target: WebRevealTarget) => Promise<string | null>;
  revealing: Set<string>;
  api: AccountApi;
  refId: string;
  onClose: () => void;
  guard?: { current: Guard | null };
  /** The connection to open first (the jump from a name's panel). */
  open?: string | null;
}) {
  const a = nameOf(edge.a);
  const b = nameOf(edge.b);
  const box = useRef<HTMLElement>(null);
  const [openId, setOpenId] = useState<string | null>(firstOpen);
  const [staged, setStaged] = useState<Record<string, Staged>>({});
  const [adding, setAdding] = useState(false);
  const [ask, setAsk] = useState<null | (() => void)>(null);
  const [saving, setSaving] = useState(false);
  const saveFlight = useRef(false);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    box.current?.focus();
    return () => void (live.current = false);
  }, []);

  const pending = edge.links.filter((l) => changesOf(l, staged[l.id]).length > 0);
  const dirty = pending.length > 0;

  // The page asks before it leaves; so does the browser when the tab is closed or reloaded.
  useEffect(() => {
    if (!guard) return;
    guard.current = (leave) => {
      if (!dirty) return false;
      setAsk(() => leave);
      return true;
    };
    return () => {
      guard.current = null;
    };
  }, [guard, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent): void => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** Runs `then` now, or after the DM has said Leave when something is unsaved (and then drops what was unsaved). */
  function leaving(then: () => void): void {
    if (dirty) setAsk(() => then);
    else then();
  }
  function stage(id: string, next: Staged): void {
    setStaged((was) => ({ ...was, [id]: { ...was[id], ...next } }));
  }

  async function save(): Promise<void> {
    if (saveFlight.current || !dirty) return;
    saveFlight.current = true;
    setSaving(true);
    setProblem(null);
    const plan = pending.flatMap((l) => changesOf(l, staged[l.id]));
    let done = 0;
    try {
      for (const change of plan) {
        const refused = await act(change, p.saved);
        if (refused !== null) {
          if (live.current) setProblem(done > 0 ? `${p.partlySaved(done, plan.length)} ${refused}`.trim() : refused);
          return;
        }
        done += 1;
      }
      if (live.current) setStaged({});
    } finally {
      saveFlight.current = false;
      if (live.current) setSaving(false);
    }
  }

  return (
    <section class="panel stack" aria-labelledby="web-line-h" tabIndex={-1} ref={box}>
      <h2 id="web-line-h">
        {p.heading(a, b)}
        {dirty && (
          <span class="small" title={p.unsaved}>
            {" "}
            <span aria-hidden="true">•</span>
            <span class="visually-hidden"> ({p.unsaved})</span>
          </span>
        )}
      </h2>
      {ask && (
        <div class="row" role="alertdialog" aria-label={p.leaveAsk}>
          <span>{p.leaveAsk}</span>
          <button
            type="button"
            class="button"
            onClick={() => {
              const go = ask;
              setAsk(null);
              setStaged({});
              go();
            }}
          >
            {p.leave}
          </button>
          <button type="button" class="button secondary" autoFocus onClick={() => setAsk(null)}>
            {p.stay}
          </button>
        </div>
      )}
      {canEdit && (
        <div class="stack">
          <button type="button" class="button secondary" aria-expanded={adding} onClick={() => setAdding((on) => !on)}>
            {adding ? p.addClose : p.add}
          </button>
          {adding && <LazyPanel key={`add-${edge.a}|${edge.b}`} load={() => import("./AddLink")} what="the form to add a connection" props={{ edge, kindOf, nameOf, act }} />}
        </div>
      )}
      {edge.links.length === 0 ? (
        <p class="muted">{p.none}</p>
      ) : (
        <ol class="stack" aria-label={p.listLabel}>
          {edge.links.map((l, i) => (
            <Line
              key={l.id}
              n={i + 1}
              link={l}
              a={a}
              b={b}
              isOpen={openId === l.id}
              dirty={changesOf(l, staged[l.id]).length > 0}
              staged={staged[l.id]}
              canEdit={canEdit}
              onToggle={() => (openId === l.id ? setOpenId(null) : leaving(() => setOpenId(l.id)))}
              onStage={(next) => stage(l.id, next)}
              act={act}
              reveal={reveal}
              revealing={revealing}
              api={api}
              refId={refId}
            />
          ))}
        </ol>
      )}
      {canEdit && dirty && (
        <div class="row">
          <button type="button" class="button" disabled={saving} onClick={() => void save()}>
            {p.save}
          </button>
          <button type="button" class="button secondary" disabled={saving} onClick={() => setStaged({})}>
            {p.discard}
          </button>
        </div>
      )}
      <SavingWait active={saving} stage={p.saving} />
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      <button type="button" class="button secondary" onClick={() => leaving(onClose)}>
        {p.close}
      </button>
    </section>
  );
}

/** One connection: its compact line, and (opened) its full options. */
function Line({
  n,
  link,
  a,
  b,
  isOpen,
  dirty,
  staged,
  canEdit,
  onToggle,
  onStage,
  act,
  reveal,
  revealing,
  api,
  refId,
}: {
  n: number;
  link: Link;
  a: string;
  b: string;
  isOpen: boolean;
  dirty: boolean;
  staged: Staged | undefined;
  canEdit: boolean;
  onToggle: () => void;
  onStage: (next: Staged) => void;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
  reveal: (target: WebRevealTarget) => Promise<string | null>;
  revealing: Set<string>;
  api: AccountApi;
  refId: string;
}) {
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const feeling = KNOWS_FEELING_LIVE ? feelingOf(link.words) : null;
  const how = KNOWS_HOW_LIVE ? knowsHowOf(link.words) : null;
  const sourceWords = words.sourceLine(link.source);
  const secret = staged?.secret ?? link.secret;
  const pinned = staged?.pinned ?? link.pinned ?? false;
  const text = staged?.words ?? link.rawWords ?? link.words;
  const line = `${p.arrow(a, b, link.direction)} · ${link.words}`;
  return (
    <li class={dimmedName(link) ? "stack web-dim" : "stack"}>
      <button type="button" class="button secondary web-line-row" aria-expanded={isOpen} aria-controls={`web-link-${link.id}`} onClick={onToggle}>
        <span aria-hidden="true">{isOpen ? "▾" : "▸"} </span>
        {markSecret(link) && <span aria-hidden="true">{words.secretMark} </span>}
        {link.belief && <span aria-hidden="true">{words.theoryMark} </span>}
        <span>{line}</span>
        {!link.knowWord && link.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
        {dirty && (
          <span class="small">
            {" "}
            <span aria-hidden="true">•</span>
            <span class="visually-hidden"> ({p.unsavedShort})</span>
          </span>
        )}
      </button>
      {isOpen && (
        <div class="stack" id={`web-link-${link.id}`}>
          {sourceWords && <p class="small muted">{sourceWords}</p>}
          {link.knowWord && words.knowWords[link.knowWord] && <p class="small muted">{words.knowWords[link.knowWord]}</p>}
          {link.belief && (
            <p class="small">
              {link.belief.status} · {words.confidence(Math.round(link.belief.confidence * 100))}
            </p>
          )}
          {link.belief?.contradicted && <p class="small warn">{words.believesFalse}</p>}
          {canEdit && link.check && <p class="small">{words.checkLine(link.check)}</p>}
          {!link.knowWord && link.revealed === false && <p class="small muted">{words.notRevealedFull}</p>}
          {canEdit && link.revealed === false && (
            <div class="row">
              <button
                type="button"
                class="button secondary"
                aria-label={words.revealLink(n)}
                disabled={revealing.has(`link:${link.id}`)}
                onClick={() => void reveal({ kind: "link", id: link.id }).then(setProblem)}
              >
                {words.reveal}
              </button>
              {problem?.endsWith(words.namesPageEnding) && (
                <a class="small" href={`/account/names/${encodeURIComponent(refId)}`}>
                  {words.openNames}
                </a>
              )}
            </div>
          )}
          {canEdit && link.revealed === false && <SavingWait active={revealing.has(`link:${link.id}`)} />}
          {canEdit && (
            <div class="stack">
              {feeling !== null ? (
                // A "knows (…)" connection takes one of the nine feelings, never free words; the picker sends at once.
                <LazyPanel load={() => import("./LinkFeeling")} what={words.feelingLoading} props={{ n, link, feeling, act }} />
              ) : (
                <label class="field">
                  {words.wordsLabel(n)}
                  <input type="text" value={text} maxLength={100} onInput={(e) => onStage({ words: (e.target as HTMLInputElement).value })} />
                </label>
              )}
              {how !== null && <LazyPanel load={() => import("./LinkKnowsHow")} what={knowsHowWords.loading} props={{ n, id: link.id, how, act }} />}
              <div class="row">
                <button
                  type="button"
                  class="button secondary"
                  aria-pressed={secret}
                  aria-label={secret ? words.makeKnown(n) : words.makeSecret(n)}
                  onClick={() => onStage({ secret: !secret })}
                >
                  {secret ? p.makeKnown : p.makeSecret}
                </button>
                <button
                  type="button"
                  class="button secondary"
                  aria-pressed={pinned}
                  aria-label={pinned ? words.unpinLabel(n) : words.pinLabel(n)}
                  onClick={() => onStage({ pinned: !pinned })}
                >
                  {pinned ? p.unpin : p.pin}
                </button>
                {!asking && (
                  <button type="button" class="button secondary" aria-label={words.removeLink(n)} onClick={() => setAsking(true)}>
                    {p.remove}
                  </button>
                )}
              </div>
              <p class="small muted">{p.stagedHint}</p>
              {WHO_KNOWS_LIVE && (link.secret || link.revealed === false) && <WhoKnows api={api} refId={refId} link={link} act={act} />}
              {asking && (
                <div class="row" role="group" aria-label={p.remove}>
                  <span>{words.removeAsk(a, b)}</span>
                  <button type="button" class="button" autoFocus onClick={() => void act({ action: "remove", id: link.id, confirm: true }, words.removed).then(setProblem)}>
                    {words.removeYes}
                  </button>
                  <button type="button" class="button secondary" onClick={() => setAsking(false)}>
                    {words.keep}
                  </button>
                </div>
              )}
              {problem && !problem.endsWith(words.namesPageEnding) && (
                <p class="warn" role="alert">
                  {problem}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

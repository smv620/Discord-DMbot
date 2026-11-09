/** @jsxImportSource preact */
// The Story Graph's line panel (#2324, #2934): every connection between two names, and (for a DM) the controls to change them:
// words, secret, pin, who knows, the feeling of a "knows" line, remove, add. Its own file, loaded when a line is chosen, so the
// graph page keeps to its weight budget as features are added. Which of its parts show is the API's rule and the
// page's switches, as before.
import { useEffect, useRef, useState } from "preact/hooks";

import { KNOWS_FEELING_LIVE } from "../content/knows-feeling-switch";
import { feelingOf } from "../content/knows-feeling-words";
import { WHO_KNOWS_LIVE } from "../content/who-knows-switch";
import { webPage as words } from "../content/web-words";
import { type AccountApi, type WebEdge, type WebLinkChange, type WebRevealTarget } from "./api";
import LazyPanel from "./LazyPanel";
import { dimmedName, markSecret } from "./know";

/** All the connections between two names, and (for a DM) the controls to change them. */
export default function LinePanel({
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
}) {
  const a = nameOf(edge.a);
  const b = nameOf(edge.b);
  const box = useRef<HTMLElement>(null);
  useEffect(() => box.current?.focus(), []);
  return (
    <section class="panel stack" aria-labelledby="web-line-h" tabIndex={-1} ref={box}>
      <h2 id="web-line-h">{words.edgePanel(a, b)}</h2>
      {/* Connections are changed right here; the full editor is each name's own row on the Names page. */}
      {canEdit && <p class="row small">
        {[edge.a, edge.b].map((id) => (
          <a key={id} class="button secondary" href={`/account/names/${encodeURIComponent(refId)}#said=${encodeURIComponent(nameOf(id))}`}>
            {words.editFull(nameOf(id))}
          </a>
        ))}
      </p>}
      {/* Every connection between the two names, in both directions (#2934): the two-way ones first, then
          one column for each way. The numbers stay the same in every group so "Words for connection 2" is unique. */}
      <div class="stack">
        {edge.links.some((l) => l.direction === "both") && (
          <section class="stack" aria-label={words.bothWays}>
            <h3 class="small">{words.bothWays}</h3>
            <ol class="stack">
              {edge.links.map((l, i) =>
                l.direction === "both" ? (
                  <LinkRow key={l.id} n={i + 1} link={l} a={a} b={b} canEdit={canEdit} act={act} reveal={reveal} revealing={revealing} api={api} refId={refId} />
                ) : null,
              )}
            </ol>
          </section>
        )}
        {edge.links.some((l) => l.direction !== "both") && (
          <div class="web-sides">
            {(["a-b", "b-a"] as const).map((way) => (
              <section key={way} class="stack" aria-label={way === "a-b" ? words.oneWay(a, b) : words.oneWay(b, a)}>
                <h3 class="small">{way === "a-b" ? words.oneWay(a, b) : words.oneWay(b, a)}</h3>
                <ol class="stack">
                  {edge.links.map((l, i) =>
                    l.direction === way ? (
                      <LinkRow key={l.id} n={i + 1} link={l} a={a} b={b} canEdit={canEdit} act={act} reveal={reveal} revealing={revealing} api={api} refId={refId} />
                    ) : null,
                  )}
                </ol>
              </section>
            ))}
          </div>
        )}
      </div>
      {canEdit && (
        <LazyPanel key={`add-${edge.a}|${edge.b}`} load={() => import("./AddLink")} what="the form to add a connection" props={{ edge, kindOf, nameOf, act }} />
      )}
      <button type="button" class="button secondary" onClick={onClose}>
        {words.close}
      </button>
    </section>
  );
}

/** "Who knows this?" on a held connection (#2935 D4, #3214): the DM says only the DM, some characters, or everyone at
 * the table, and the bot applies it. Shown to the DM's full view only, behind WHO_KNOWS_LIVE. */
function WhoKnows({
  api,
  refId,
  link,
  act,
}: {
  api: AccountApi;
  refId: string;
  link: WebEdge["links"][number];
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
}) {
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [names, setNames] = useState<Map<string, string>>(new Map()); // the names of the characters, by id
  const [q, setQ] = useState("");
  const [list, setList] = useState<{ id: string; name: string }[]>([]);
  const [total, setTotal] = useState(0);
  const [failed, setFailed] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [already, setAlready] = useState("");
  const [busy, setBusy] = useState(false); // a change is with the bot: no second press until it is answered
  const latest = useRef(0);
  // Undefined from an older API: no answer is pressed, and nothing is said about one (#3214).
  const current = link.who;
  const holders = link.knownBy ?? [];

  // The character search: the same wait and list as the See as… picker.
  useEffect(() => {
    if (!picking || !api.webViewers) return;
    const mine = ++latest.current;
    const timer = setTimeout(
      () => {
        api
          .webViewers!(refId, q.trim())
          .then((got) => {
            if (mine !== latest.current) return;
            setList(got.viewers);
            setTotal(got.total);
            setFailed(false);
          })
          .catch(() => {
            if (mine !== latest.current) return;
            setList([]);
            setTotal(0);
            setFailed(true);
          });
      },
      q === "" ? 0 : 300,
    );
    return () => clearTimeout(timer);
  }, [api, refId, q, picking]);

  const stateLine =
    current === undefined
      ? null
      : current === "dm"
        ? words.whoStateDm
        : current === "some" && holders.length > 0
          ? words.whoStateSome(words.whoNames(holders.map((h) => h.name)))
          : current === "all"
            ? words.whoStateAll
            : words.whoStateDm;

  /** Only the DM, or everyone at the table: sent at once. Pressing the answer already set says so, and sends nothing. */
  async function choose(who: "dm" | "everyone"): Promise<void> {
    if (busy) return;
    const now = current === "dm" ? "dm" : current === "all" ? "everyone" : null;
    setAlready("");
    if (now === who) return setAlready(words.whoAlready);
    setBusy(true);
    try {
      setProblem(await act({ action: "who", id: link.id, who }, who === "dm" ? words.whoDoneDm : words.whoDoneAll));
    } finally {
      setBusy(false);
    }
  }

  function toggle(v: { id: string; name: string }): void {
    setChosen((was) => {
      const next = new Set(was);
      if (next.has(v.id)) next.delete(v.id);
      else next.add(v.id);
      return next;
    });
    setNames((was) => new Map(was).set(v.id, v.name));
  }

  async function save(): Promise<void> {
    if (busy) return;
    const ids = [...chosen];
    // The same set as already holds it: nothing is sent, and the page says so.
    const same = current === "some" && ids.length === holders.length && holders.every((h) => chosen.has(h.id));
    setAlready("");
    if (same) return setAlready(words.whoAlready);
    const named = ids.map((id) => names.get(id) ?? holders.find((h) => h.id === id)?.name ?? "");
    setBusy(true);
    try {
      const problemNow = await act({ action: "who", id: link.id, who: "some", characters: ids }, words.whoDoneSome(words.whoNames(named)));
      setProblem(problemNow);
      if (problemNow === null) setPicking(false); // a failure keeps the picker and the ticks
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="stack">
      <p>
        <strong>{words.whoKnowsHeading}</strong>
      </p>
      {stateLine && <p class="small">{stateLine}</p>}
      <div class="row" role="group" aria-label={words.whoKnowsHeading}>
        <button type="button" class="button secondary" aria-pressed={current === "dm"} disabled={busy} onClick={() => void choose("dm")}>
          {words.whoOnlyDm}
        </button>
        <button
          type="button"
          class="button secondary"
          aria-pressed={current === "some"}
          disabled={busy}
          onClick={() => {
            setChosen(new Set(holders.map((h) => h.id)));
            setNames(new Map(holders.map((h) => [h.id, h.name])));
            setPicking(true);
          }}
        >
          {words.whoSome}
        </button>
        <button type="button" class="button secondary" aria-pressed={current === "all"} disabled={busy} onClick={() => void choose("everyone")}>
          {words.whoEveryone}
        </button>
      </div>
      {already && <p role="status">{already}</p>}
      {problem && <p role="status">{problem}</p>}
      {picking && (
        <div class="stack">
          <label>
            {words.whoSearch}
            <input
              type="search"
              value={q}
              placeholder={words.whoSearch}
              autoComplete="off"
              spellcheck={false}
              onInput={(e) => setQ((e.target as HTMLInputElement).value)}
            />
          </label>
          {failed && <p role="status">{words.seeAsLoadFailed}</p>}
          <ul class="web-found" aria-label={words.whoSomeLabel}>
            {list.map((v) => (
              <li key={v.id}>
                <label class="choice">
                  <input type="checkbox" checked={chosen.has(v.id)} onChange={() => toggle(v)} />
                  <span>{v.name}</span>
                </label>
              </li>
            ))}
          </ul>
          {total > list.length && <p class="small muted">{words.seeAsMore(list.length, total)}</p>}
          <div class="row">
            <button type="button" class="button" disabled={chosen.size === 0 || busy} onClick={() => void save()}>
              {words.whoSave}
            </button>
            {chosen.size === 0 && <span class="small muted">{words.whoPickOne}</span>}
          </div>
        </div>
      )}
    </div>
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
  revealing,
  api,
  refId,
}: {
  n: number;
  link: WebEdge["links"][number];
  a: string;
  b: string;
  canEdit: boolean;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
  reveal: (target: WebRevealTarget) => Promise<string | null>;
  revealing: Set<string>;
  api: AccountApi;
  refId: string;
}) {
  const [text, setText] = useState(link.rawWords ?? link.words);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const run = async (change: WebLinkChange, ok: string): Promise<void> => setProblem(await act(change, ok));
  const sourceWords = words.sourceLine(link.source);
  const feeling = KNOWS_FEELING_LIVE ? feelingOf(link.words) : null;
  return (
    <li class={dimmedName(link) ? "stack web-dim" : "stack"}>
      <p>
        {markSecret(link) && <span aria-hidden="true">{words.secretMark} </span>}
        {link.belief && <span aria-hidden="true">{words.theoryMark} </span>}
        {words.linkText(a, b, link.words, link.direction)}
        {!link.knowWord && link.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
      </p>
      {sourceWords && <p class="small muted">{sourceWords}</p>}
      {link.knowWord && words.knowWords[link.knowWord] && <p class="small muted">{words.knowWords[link.knowWord]}</p>}
      {canEdit && link.pinned && <p class="small">{words.pinMark} {words.pinnedWords}</p>}
      {canEdit && link.check && <p class="small">{words.checkLine(link.check)}</p>}
      {link.belief && (
        <p class="small">
          {link.belief.status} · {words.confidence(Math.round(link.belief.confidence * 100))}
        </p>
      )}
      {link.belief?.contradicted && <p class="small warn">{words.believesFalse}</p>}
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
      {canEdit && (
        <div class="stack">
          {feeling !== null ? (
            // A "knows (…)" connection takes one of the nine feelings, never free words (they would make it a catch-all).
            <LazyPanel load={() => import("./LinkFeeling")} what={words.feelingLoading} props={{ n, link, feeling, act }} />
          ) : (
            <label class="field">
              {words.wordsLabel(n)}
              <input type="text" value={text} maxLength={100} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
            </label>
          )}
          <div class="row">
            {feeling === null && (
              <button
                type="button"
                class="button secondary"
                aria-label={words.saveWords(n)}
                onClick={() => void run({ action: "words", id: link.id, words: text }, words.saved)}
              >
                {words.save}
              </button>
            )}
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
          {canEdit && WHO_KNOWS_LIVE && (link.secret || link.revealed === false) && (
            <WhoKnows api={api} refId={refId} link={link} act={act} />
          )}
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


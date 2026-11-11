/** @jsxImportSource preact */
// "👣 Met but not named" on the Names page (#4084, the web half of #4045): the creatures and people the party met at the table that
// nobody named. For each, the DM gives them a name, says they are a name the campaign already has ("Same as…"), or removes it. DM
// only (the list is the DM's alone). Each answer is queued for the bot like every name action, and the wait shows what stage it is
// at, how long it has run and, after a minute, that it is slow. The page shows the words the party said, how many and the date of
// the session: never a line of the transcript or who spoke it.
import { useEffect, useRef, useState } from "preact/hooks";

import { metWords as w } from "../content/met-list-words";
import { ApiError, type AccountApi, type MetAnswer, type MetItem, type NameRow } from "./api";
import SavingWait from "./SavingWait";
import { kindStyle } from "./webKinds";

/** Only a person or a group can be joined to (core refuses a place or a thing). */
const joinable = (n: NameRow): boolean => ["npc", "player_character", "group"].includes(kindStyle(n.kind));

const dateOf = (at: number | null): string =>
  at === null ? "" : new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

type Open = { id: string; mode: "name" | "same" } | null;
type Same = { id: string; name: string; entry: string | null } | null; // a name the campaign already has, and the entry it is (when it can be found)

export default function MetList({ api, campaignRef, onChanged }: { api: AccountApi; campaignRef: string; onChanged?: () => void }) {
  const [list, setList] = useState<MetItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [notYours, setNotYours] = useState(false); // not a DM of the campaign: the list is simply not there
  const [open, setOpen] = useState<Open>(null);
  const [text, setText] = useState("");
  const [found, setFound] = useState<NameRow[] | null>(null);
  const [same, setSame] = useState<Same>(null);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState(""); // guidance after "No, different": a plain line, not a success
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  const looks = useRef(0);
  const seeks = useRef(0); // the newest search wins, so a slow answer cannot overwrite a newer one
  useEffect(() => {
    alive.current = true;
    return () => void (alive.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.metList) return;
    const mine = (looks.current += 1);
    try {
      const got = await api.metList(campaignRef);
      if (alive.current && mine === looks.current) (setList(got), setFailed(false));
    } catch (error) {
      if (!alive.current || mine !== looks.current) return;
      if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) setNotYours(true);
      else setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  const sayFailure = (error: unknown): string => {
    if (!(error instanceof ApiError)) return w.couldNot;
    if (error.kind === "gone" || error.kind === "not-found") return w.gone;
    if (error.kind === "not-allowed") return w.notAllowed;
    if (error.kind === "signed-out") return w.signedOut;
    if (error.kind === "met-waiting") return w.waitingName;
    if (error.kind === "bad-name") return w.badName;
    if (error.kind === "bad-target") return w.cantJoin;
    if (error.kind === "still-waiting") return w.stillWorking;
    return w.couldNot;
  };

  /** Sends one answer; resolves true when the bot did it. A name the campaign already has opens the question instead of failing. */
  async function send(item: MetItem, answer: MetAnswer, done: string): Promise<boolean> {
    if (flight.current || !api.answerMet) return false;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    let ok = false;
    try {
      await api.answerMet(campaignRef, item.id, answer);
      ok = true;
      if (alive.current) (setSaid({ ok: true, text: done }), setOpen(null), setSame(null), setText(""), setFound(null));
    } catch (error) {
      if (error instanceof ApiError && error.kind === "name-exists" && answer.action === "name") {
        // Offer "the same one" when the name can be found, so the DM does not have to search for it.
        let entry: string | null = null;
        try {
          const page = await api.allNames?.(campaignRef, { q: answer.name, kind: "", sort: "name", page: 1 });
          entry = page?.names.find((n) => joinable(n) && n.name.toLowerCase() === answer.name.trim().toLowerCase())?.id ?? null;
        } catch {
          entry = null;
        }
        if (alive.current) setSame({ id: item.id, name: answer.name.trim(), entry });
      } else if (alive.current) {
        setSaid({ ok: false, text: sayFailure(error) });
        if (error instanceof ApiError && (error.kind === "gone" || error.kind === "not-found")) (setOpen(null), setSame(null));
        if (error instanceof ApiError && error.kind === "bad-target") setSame(null);
      }
    } finally {
      await read(); // (what shows is always the API's own)
      if (ok) onChanged?.();
      flight.current = false;
      if (alive.current) setBusy(false);
    }
    return ok;
  }

  async function search(q: string): Promise<void> {
    setText(q);
    const mine = (seeks.current += 1);
    if (q.trim().length < 2 || !api.allNames) return setFound(null);
    try {
      const page = await api.allNames(campaignRef, { q: q.trim(), kind: "", sort: "name", page: 1, size: 20 });
      if (alive.current && mine === seeks.current) setFound(page.names.filter(joinable));
    } catch {
      if (alive.current && mine === seeks.current) setFound([]);
    }
  }

  if (!api.metList || notYours) return null;
  if (failed && !list) return <p class="warn" role="alert">{w.failed}</p>;
  if (!list) return <p class="muted" role="status">{w.loading}</p>;
  return (
    <section class="panel stack" aria-labelledby="met-h">
      <h2 id="met-h">
        {w.heading}
        {list.length > 0 ? ` (${list.length})` : ""}
      </h2>
      <p class="small">{w.lead}</p>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? said.text : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? said.text : ""}
      </p>
      {list.length === 0 && <p class="muted">{w.empty}</p>}
      <ul class="stack">
        {list.map((m) => {
          const what = w.label(m.what, m.count);
          const mine = open?.id === m.id ? open.mode : null;
          return (
            <li key={m.id} class="stack">
              <p id={`met-${m.id}`}>
                <strong>{what}</strong>
                {m.sessionStartedAt !== null ? <span class="small muted"> · {w.when(dateOf(m.sessionStartedAt))}</span> : ""}
              </p>
              {same?.id === m.id ? (
                <div class="stack">
                  <p>{w.exists(same.name, what)}</p>
                  {same.entry === null && <p class="small">{w.useSame}</p>}
                  <div class="row">
                    {same.entry !== null && (
                      <button type="button" class="button" disabled={busy} onClick={() => void send(m, { action: "join", target: same.entry! }, w.joined(what, same.name))}>
                        {w.yesSame}
                      </button>
                    )}
                    {same.entry === null && (
                      <button type="button" class="button" disabled={busy} onClick={() => (setSame(null), setOpen({ id: m.id, mode: "same" }), setText(""), setFound(null))}>
                        {w.same}
                      </button>
                    )}
                    <button type="button" class="button secondary" disabled={busy} onClick={() => (setSame(null), setOpen({ id: m.id, mode: "name" }), setText(""), setHint(w.different))}>
                      {w.noDifferent}
                    </button>
                  </div>
                </div>
              ) : (
                <div class="row">
                  <button type="button" class="button" disabled={busy} aria-describedby={`met-${m.id}`} onClick={() => (setOpen({ id: m.id, mode: "name" }), setText(""), setFound(null), setSaid(null), setHint(""))}>
                    {w.give}
                  </button>
                  <button type="button" class="button secondary" disabled={busy} aria-describedby={`met-${m.id}`} onClick={() => (setOpen({ id: m.id, mode: "same" }), setText(""), setFound(null), setSaid(null))}>
                    {w.same}
                  </button>
                  <button type="button" class="button secondary" disabled={busy} aria-describedby={`met-${m.id}`} onClick={() => void send(m, { action: "remove" }, w.removed(what))}>
                    {w.remove}
                  </button>
                </div>
              )}
              {same === null && mine === "name" && (
                <form
                  class="row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (text.trim() !== "") void send(m, { action: "name", name: text }, w.named(what, text.trim()));
                  }}
                >
                  {hint !== "" && <p class="small">{hint}</p>}
                  <label class="field">
                    {w.nameLabel(what)}
                    <input type="text" name="name" maxLength={100} value={text} disabled={busy} autoFocus onInput={(e) => setText((e.target as HTMLInputElement).value)} />
                  </label>
                  <button type="submit" class="button" disabled={busy || text.trim() === ""}>
                    {w.save}
                  </button>
                  <button type="button" class="button secondary" disabled={busy} onClick={() => setOpen(null)}>
                    {w.cancel}
                  </button>
                </form>
              )}
              {same === null && mine === "same" && (
                <div class="stack">
                  <label class="field">
                    {w.sameLabel}
                    <input type="search" name="same" value={text} disabled={busy} autoFocus onInput={(e) => void search((e.target as HTMLInputElement).value)} />
                  </label>
                  <p class="small muted">{w.sameHint}</p>
                  <div role="status" aria-live="polite">{found !== null && found.length === 0 && <p class="small">{w.sameNone}</p>}</div>
                  <ul class="stack">
                    {(found ?? []).map((n) => (
                      <li key={n.id}>
                        <button type="button" class="button secondary" disabled={busy} aria-describedby={`met-${m.id}`} onClick={() => void send(m, { action: "join", target: n.id }, w.joined(what, n.name))}>
                          {n.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                  <div class="row">
                    <button type="button" class="button secondary" disabled={busy} onClick={() => setOpen(null)}>
                      {w.cancel}
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <SavingWait active={busy} stage={w.saving} />
    </section>
  );
}

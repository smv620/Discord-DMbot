/** @jsxImportSource preact */
// A place's "↔️ Next to…" and "🛣️ Travel to…" on a name of the Names page (#4341, the website half of #4173 slice 6d): search for the other
// place, pick the words that finish the sentence (the card's place first, so a one-way word is never saved the wrong way round), and Save.
// The same words as Discord's place card (content/place-routes-words.ts). Its own file, fetched only when a DM opens a place; shown by the
// Names page only for a place, to a DM, while PLACE_ROUTES_LIVE. Saving is the page's usual add-a-connection call: one change, one Undo.
import { useEffect, useRef, useState } from "preact/hooks";

import { NEXT_CHOICES, placeRoutesWords as w, TRAVEL_CHOICES, type Choice } from "../content/place-routes-words";
import { ApiError, type AccountApi, type NameRow } from "./api";

const SEARCH_MS = 250;
type Other = { id: string; name: string };

export default function PlaceRoutes({ api, refId, row, onSignedOut }: { api: AccountApi; refId: string; row: NameRow; onSignedOut?: () => void }) {
  const [how, setHow] = useState<"next" | "travel" | null>(null);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Other[] | null>(null);
  const [looking, setLooking] = useState(false);
  const [searchFailed, setSearchFailed] = useState(false);
  const [picked, setPicked] = useState<Other | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const flight = useRef(false);
  const seq = useRef(0);
  const travel = how === "travel";

  // Focus must not fall off the page when the control that had it goes away: after a place is picked, the choices' legend takes it;
  // after the panel closes (Back, or a save that worked) and it was opened from a button, that button takes it again.
  // once the panel has been opened from one of its buttons (wasOpen), so a page that loads does not steal focus.
  const legendRef = useRef<HTMLLegendElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const travelRef = useRef<HTMLButtonElement>(null);
  const openedBy = useRef<"next" | "travel">("next");
  const wasOpen = useRef(false);
  useEffect(() => {
    if (picked !== null) legendRef.current?.focus();
  }, [picked]);
  useEffect(() => {
    if (how !== null) {
      openedBy.current = how;
      wasOpen.current = true;
      return;
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      (openedBy.current === "travel" ? travelRef : nextRef).current?.focus();
    }
  }, [how]);

  useEffect(() => {
    const mine = ++seq.current;
    setSearchFailed(false);
    setLooking(false);
    if (how === null || picked !== null || q.trim() === "" || !api.allNames) {
      setFound(null);
      return;
    }
    setFound(null); // (old results never stay clickable for new words)
    setLooking(true);
    const timer = setTimeout(() => {
      api
        .allNames?.(refId, { q, kind: "place", sort: "name", page: 1 })
        .then((read) => {
          if (mine !== seq.current) return;
          setFound(read.names.filter((n) => n.id !== row.id).map((n) => ({ id: n.id, name: n.name })));
          setLooking(false);
        })
        .catch(() => {
          if (mine === seq.current) {
            setFound(null);
            setSearchFailed(true);
            setLooking(false);
          }
        });
    }, SEARCH_MS);
    return () => clearTimeout(timer);
  }, [api, refId, how, q, picked, row.id]);

  const close = (): void => {
    setHow(null);
    setQ("");
    setFound(null);
    setPicked(null);
    setChoice(null);
  };

  async function save(): Promise<void> {
    if (flight.current || !picked || !choice || !api.changeWebLink) return;
    flight.current = true;
    setBusy(true);
    try {
      // the card's place is the connection's first name, so a one-way word keeps its meaning
      await api.changeWebLink(refId, { action: "add", a: row.id, b: picked.id, words: choice.forward, secret: false });
      setNote({ ok: true, text: w.saved(row.name, picked.name, choice) });
      close();
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      setNote({ ok: false, text: error instanceof ApiError && error.kind === "bad-target" ? w.refused : w.failed });
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  const choices = travel ? TRAVEL_CHOICES : NEXT_CHOICES;
  return (
    <section class="stack" aria-label={w.nextButton}>
      {how === null && (
        <p class="row">
          <button ref={nextRef} type="button" class="button secondary" onClick={() => { setNote(null); setHow("next"); }}>{w.nextButton}</button>
          <button ref={travelRef} type="button" class="button secondary" onClick={() => { setNote(null); setHow("travel"); }}>{w.travelButton}</button>
        </p>
      )}
      {how !== null && picked === null && (
        <div class="stack">
          <label class="field">
            {w.asked(row.name, travel)}
            <input type="text" value={q} onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
          </label>
          {looking && <p class="small muted" role="status">{w.searching}</p>}
          {searchFailed && <p class="small warn" role="alert">{w.failedSearch}</p>}
          {found !== null && found.length === 0 && <p class="small muted">{w.none}</p>}
          {found !== null && found.length > 0 && (
            <ul class="stack">
              {found.map((o) => (
                <li key={o.id}>
                  <button type="button" class="button secondary" onClick={() => setPicked(o)}>{o.name}</button>
                </li>
              ))}
            </ul>
          )}
          <button type="button" class="button secondary" onClick={close}>{w.back}</button>
        </div>
      )}
      {how !== null && picked !== null && (
        <fieldset class="stack" aria-busy={busy}>
          <legend ref={legendRef} tabIndex={-1}>{w.ask(row.name, picked.name, travel)}</legend>
          {choices.map((c) => (
            <label key={c.label} class="choice">
              <input type="radio" name={`route-${row.id}`} checked={choice === c} onChange={() => setChoice(c)} />
              <span>
                {c.label}
                <span class="small muted"> {w.hint(row.name, picked.name, c)}</span>
              </span>
            </label>
          ))}
          <p class="row">
            <button type="button" class="button" disabled={choice === null || busy} onClick={() => void save()}>{w.save}</button>
            <button type="button" class="button secondary" onClick={close}>{w.back}</button>
          </p>
        </fieldset>
      )}
      {note && <p class={note.ok ? "small" : "small warn"} role={note.ok ? "status" : "alert"}>{note.text}</p>}
    </section>
  );
}

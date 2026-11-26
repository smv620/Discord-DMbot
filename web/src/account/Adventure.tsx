/** @jsxImportSource preact */
// "Your adventure" on the Upload lore page (#3694 slices A and B, behind ADVENTURE_LIVE; core #5300 and #5305): the files the campaign's
// Shared story holds and how far each read is (counts, times and the file's name, never its text), and what was read from them, fifty
// at a time. Both are the DM's alone: the doors answer anyone else with "not found", and then this shows nothing at all. The words
// are Discord's (dm_screen/adventure_card.py). The book's own words appear only here, are never logged, and are never kept by the
// page beyond what is on the screen. Its own file, loaded only when the Lore page opens it, so the page keeps its weight budget.
import { useEffect, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AccountApi, type AdventureDocument, type AdventureItem, type AdventureList, ApiError } from "./api";

/** Where a file's reading stands, in Discord's words ("not read yet" when it has not been). */
export function readLine(d: AdventureDocument): string {
  const read = d.read;
  if (!read) return w.notRead;
  if (read.status === "done") {
    const f = d.found;
    const found = [
      w.count(f.chapters, w.chapter[0], w.chapter[1]),
      w.count(f.places, w.place[0], w.place[1]),
      w.count(f.people, w.person[0], w.person[1]),
      w.count(f.links, w.link[0], w.link[1]),
    ].join(", ");
    return w.readOf(found, read.skippedParts);
  }
  const percent = read.totalChunks > 0 ? Math.min(100, Math.floor((read.nextChunk / read.totalChunks) * 100)) : 0;
  if (read.status === "reading") return w.reading(percent);
  return w.stopped(percent, w.why[read.stopReason] ?? w.carryOn);
}

export default function Adventure({ api, refId }: { api: AccountApi; refId: string }) {
  const [list, setList] = useState<AdventureList | null>(null);
  const [gone, setGone] = useState(false); // (not a DM, or the switch is off: nothing is shown at all)
  const [failed, setFailed] = useState(false);
  const [tries, setTries] = useState(0);
  const [seeing, setSeeing] = useState(false);
  const [items, setItems] = useState<AdventureItem[]>([]);
  const [total, setTotal] = useState(0);
  const [looking, setLooking] = useState(false);
  const [lookFailed, setLookFailed] = useState(false);

  useEffect(() => {
    if (!api.adventure) return;
    let live = true;
    setFailed(false);
    api.adventure(refId).then(
      (got) => live && setList(got),
      (error: unknown) => {
        if (!live) return;
        if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setGone(true);
        else setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, refId, tries]);

  async function look(offset: number): Promise<void> {
    if (!api.adventureRead || looking) return;
    setLooking(true);
    setLookFailed(false);
    try {
      const got = await api.adventureRead(refId, offset);
      setTotal(got.total);
      setItems((now) => (offset === 0 ? got.items : [...now, ...got.items]));
    } catch {
      setLookFailed(true);
    } finally {
      setLooking(false);
    }
  }

  if (!api.adventure || gone) return null;
  const anyRead = (list?.documents ?? []).some((d) => d.read !== null);
  return (
    <section class="stack" aria-labelledby="adventure-h">
      <h3 id="adventure-h">{w.heading}</h3>
      {list === null && !failed && (
        <p class="muted" role="status">
          {w.loading}
        </p>
      )}
      {failed && (
        <div class="stack">
          <p class="warn" role="alert">
            {w.down}
          </p>
          <button type="button" class="button secondary" onClick={() => setTries((n) => n + 1)}>
            {w.tryAgain}
          </button>
        </div>
      )}
      {list && list.mode === "off" && <p>{w.notOn}</p>}
      {list && list.mode !== "off" && (
        <div class="stack">
          <p class="small">{w.intro}</p>
          {list.documents.length === 0 ? (
            <p>{w.empty}</p>
          ) : (
            <>
              <ol class="stack" aria-label={w.list}>
                {list.documents.map((d) => (
                  <li key={d.id}>
                    <strong>{d.name}</strong> ({w.pages(d.characters)}
                    {d.restored ? `, ${w.giveAgain}` : `, ${readLine(d)}`})
                  </li>
                ))}
              </ol>
              <p class="small muted">{w.heldMore(list.charactersHeld, list.charactersMax)}</p>
            </>
          )}
          {anyRead && !seeing && (
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setSeeing(true);
                void look(0);
              }}
            >
              {w.see}
            </button>
          )}
          {seeing && (
            <div class="stack">
              <button type="button" class="button secondary" onClick={() => (setSeeing(false), setItems([]))}>
                {w.hide}
              </button>
              {looking && items.length === 0 && (
                <p class="muted" role="status">
                  {w.seeing}
                </p>
              )}
              {lookFailed && (
                <p class="warn" role="alert">
                  {w.down}
                </p>
              )}
              {!looking && !lookFailed && total === 0 && <p>{w.nothingRead}</p>}
              {items.length > 0 && (
                <>
                  <p class="small muted">{w.of(items.length, total)}</p>
                  <ul class="stack">
                    {items.map((it, i) => (
                      <li key={i}>
                        <span class="small muted">{w.kinds[it.kind] ?? it.kind}: </span>
                        <strong>{it.name}</strong>
                        {it.summary && <span> {it.summary}</span>}
                        {it.quote && <q class="small muted"> {it.quote}</q>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {items.length < total && (
                <button type="button" class="button secondary" aria-busy={looking} onClick={() => void look(items.length)}>
                  {w.more}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

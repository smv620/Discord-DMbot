/** @jsxImportSource preact */
// The Session notes panel on the Transcripts page (#3027): a game master sees a numbered list of what happened in each
// game that has notes. Held behind SESSION_NOTES_LIVE: with the switch off, for a player, an older API or a list that
// fails to load, the panel shows nothing at all. It never shows a line of the transcript.
import { useEffect, useState } from "preact/hooks";

import { sessionDay } from "../content/recaps";
import { SESSION_NOTES_LIVE } from "../content/session-notes-switch";
import { sessionNotesWords as w } from "../content/session-notes-words";
import type { AccountApi, SessionNotes, SessionNotesItem } from "./api";

type Opened = "loading" | "failed" | SessionNotes;

export default function SessionNotesPanel({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<SessionNotesItem[] | "loading" | "hidden">("loading");
  // Each row's notes: once loaded they stay, so Open again shows them without asking the server a second time.
  const [notes, setNotes] = useState<Record<number, Opened>>({});
  const [shown, setShown] = useState<Record<number, boolean>>({});

  useEffect(() => {
    let gone = false;
    setList("loading");
    setNotes({});
    setShown({});
    if (!SESSION_NOTES_LIVE || !api.sessionNotesList) {
      setList("hidden");
      return;
    }
    api
      .sessionNotesList(campaignRef)
      .then((got) => !gone && setList(got.sessions))
      .catch(() => !gone && setList("hidden")); // a player, an older API or a failed list: nothing to show
    return () => {
      gone = true;
    };
  }, [api, campaignRef]);

  if (list === "hidden" || list === "loading") return null;

  const fetchNotes = (at: number): void => {
    setNotes((n) => ({ ...n, [at]: "loading" }));
    api
      .sessionNotes!(campaignRef, at)
      .then((got) => setNotes((n) => ({ ...n, [at]: got })))
      .catch(() => setNotes((n) => ({ ...n, [at]: "failed" })));
  };
  const toggle = (item: SessionNotesItem): void => {
    const open = !shown[item.sessionAt];
    setShown((s) => ({ ...s, [item.sessionAt]: open }));
    if (open && notes[item.sessionAt] === undefined) fetchNotes(item.sessionAt);
  };

  return (
    <section class="stack" aria-labelledby="session-notes-heading">
      <h3 id="session-notes-heading">{w.heading}</h3>
      <p class="small">{w.intro}</p>
      {list.length > 0 && (
        <ul class="campaigns">
          {list.map((item) => {
            const day = sessionDay(item.sessionAt);
            const title = w.title(item.sessionNumber, day);
            const isOpen = !!shown[item.sessionAt];
            return (
              <li key={item.sessionAt} class="stack">
                <div>
                  <span class="campaign-name">{title}</span>{" "}
                  <span class="small">{w.line(day, item.beats)}</span>{" "}
                  <button
                    type="button"
                    class="secondary"
                    aria-expanded={isOpen}
                    aria-label={isOpen ? w.hideFor(title) : w.openFor(title)}
                    onClick={() => toggle(item)}
                  >
                    {isOpen ? w.hide : w.open}
                  </button>
                </div>
                {isOpen && <Beats got={notes[item.sessionAt]} onRetry={() => fetchNotes(item.sessionAt)} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Beats({ got, onRetry }: { got: Opened | undefined; onRetry: () => void }) {
  if (got === undefined || got === "loading") {
    return (
      <p class="small muted" role="status">
        {w.opening}
      </p>
    );
  }
  if (got === "failed") {
    return (
      <p class="panel" role="status">
        {w.failed}{" "}
        <button type="button" class="secondary" onClick={onRetry}>
          {w.tryAgain}
        </button>
      </p>
    );
  }
  if (got.beats.length === 0) return <p class="small muted">{w.empty}</p>;
  return (
    <ol>
      {got.beats.map((b) => {
        const details = [b.who.length > 0 ? w.who(b.who.join(", ")) : "", b.where ? w.where(b.where) : ""].filter(Boolean);
        return (
          <li key={b.number} value={b.number}>
            {b.what}
            {details.length > 0 && <p class="small muted">{details.join(" · ")}</p>}
          </li>
        );
      })}
    </ol>
  );
}

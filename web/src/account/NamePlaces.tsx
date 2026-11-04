/** @jsxImportSource preact */
// "Where it was said" under a name to check (#2403): the windows of transcript the door sends for the DM.
// The words are drawn from the answer and dropped with the card: nothing is kept, cached or logged here.
import { useState } from "preact/hooks";

import { namePlaceWords as words } from "../content/name-places";
import type { NamePlace } from "./api";

export default function NamePlaces({
  places,
  more,
  campaignRef,
}: {
  places: NamePlace[];
  more: number;
  campaignRef: string;
}) {
  const [open, setOpen] = useState(false);
  if (places.length === 0 && more <= 0) return null;
  // The door sends the newest three places; the cap only guards the page.
  const shown = places.slice(0, 3);
  return (
    <div class="stack">
      <p>
        <button
          type="button"
          class="button secondary"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {words.button}
        </button>
      </p>
      {open && (
        <>
          {more > 0 && (
            <p class="small muted">
              {words.heard(shown.length + more, shown.length)}
            </p>
          )}
          <ul class="name-places">
            {shown.map((place, index) => (
              <li key={`${place.session}-${index}`} class="small">
                {!place.available || place.lines.length === 0 ? (
                  <span class="muted">{words.gone(place.session)}</span>
                ) : (
                  <>
                    <span class="muted">{words.session(place.session)}</span>
                    <ul>
                      {/* The door sends at most three lines a window; the cap only guards the page. */}
                      {place.lines.slice(0, 3).map((line, i) =>
                        line.removed ? (
                          <li key={i} class="muted">
                            {words.lineRemoved}
                          </li>
                        ) : (
                          <li key={i}>
                            <span class="muted">
                              {line.speaker} {words.time(line.at)}
                            </span>{" "}
                            {line.text}
                          </li>
                        ),
                      )}
                    </ul>
                    <a
                      href={`/account/transcripts/${encodeURIComponent(campaignRef)}/${place.session}`}
                    >
                      {words.openTranscriptFor(place.session)}
                    </a>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

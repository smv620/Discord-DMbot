/** @jsxImportSource preact */
// "Where it was said" under a name to check (#2403, #3384): the windows of transcript the door sends for the
// DM, newest first. The newest place is always drawn, so the context is there without a click; the
// "Where was it said?" button shows the rest, up to three in all. The words are drawn from the answer and
// dropped with the card: nothing is kept, cached or logged here.
import { useState } from "preact/hooks";

import { namePlaceWords as words } from "../content/name-places";
import type { NamePlace } from "./api";

function Place({
  place,
  campaignRef,
}: {
  place: NamePlace;
  campaignRef: string;
}) {
  return (
    <li class="small">
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
  );
}

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
  const [newest, ...rest] = shown;
  // The button is there when there is something behind it: more shown places, or more heard than shown.
  const hasMore = rest.length > 0 || more > 0;
  return (
    <div class="stack">
      {newest && (
        <ul class="name-places">
          <Place place={newest} campaignRef={campaignRef} />
        </ul>
      )}
      {hasMore && (
        <p>
          <button
            type="button"
            class="button secondary"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            <span aria-hidden="true">{words.buttonIcon}</span> {words.button}
          </button>
        </p>
      )}
      {hasMore && open && (
        <>
          {more > 0 && (
            <p class="small muted">
              {words.heard(shown.length + more, shown.length)}
            </p>
          )}
          <ul class="name-places">
            {rest.map((place, index) => (
              <Place
                key={`${place.session}-${index}`}
                place={place}
                campaignRef={campaignRef}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

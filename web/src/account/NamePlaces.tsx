/** @jsxImportSource preact */
// "Where it was said" under a name to check (#2403, #3384): the windows of transcript the door sends for the
// DM, newest first. The newest place is always drawn, so the context is there without a click; the
// "Where was it said?" button shows the rest, up to three in all. The words are drawn from the answer and
// dropped with the card: nothing is kept, cached or logged here.
import { useState } from "preact/hooks";

import { namePlaceWords as words } from "../content/name-places";
import type { NameOrigin, NamePlace } from "./api";

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

/** Where an entry came from when no sentence is kept (#4557): at most three, newest first as the door sends them. */
function Origin({ origin }: { origin: NameOrigin }) {
  if (origin.kind === "yours") return <li class="small">{words.yours}</li>;
  if (origin.kind === "upload")
    return (
      <li class="small">
        <span aria-hidden="true">{words.uploadIcon} </span>
        {words.uploadFrom}
        <em>{origin.label}</em>
        {words.uploadAfter(words.day(origin.at))}
      </li>
    );
  return (
    <li class="small">
      <em>{words.sessionFrom(origin.session, origin.at > 0 ? words.day(origin.at) : null)}</em>
      {origin.lines.length === 0 ? (
        <p class="muted">{words.originLineRemoved}</p>
      ) : (
        origin.lines.slice(0, 3).map((line, i) => (
          <blockquote key={i}>“{line}”</blockquote>
        ))
      )}
    </li>
  );
}

export default function NamePlaces({
  places,
  more,
  campaignRef,
  origins = [],
}: {
  places: NamePlace[];
  more: number;
  campaignRef: string;
  origins?: NameOrigin[];
}) {
  const [open, setOpen] = useState(false);
  if (places.length === 0 && more <= 0 && origins.length === 0) return null;
  // The door sends the newest three places; the cap only guards the page.
  const shown = places.slice(0, 3);
  const [newest, ...rest] = shown;
  // The button is there when there is something behind it: more shown places, or more heard than shown.
  const hasMore = rest.length > 0 || more > 0;
  return (
    <div class="stack">
      {origins.length > 0 && (
        <ul class="name-places">
          {origins.slice(0, 3).map((origin, i) => (
            <Origin key={i} origin={origin} />
          ))}
        </ul>
      )}
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

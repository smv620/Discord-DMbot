/** @jsxImportSource preact */
// "What's switched on" in a campaign's settings (#3693): read-only. The bot writes the finished list (names and
// plain sentences only) and the website's server hands it to a campaign's game masters; nothing here can change
// a switch, and nothing in it is a key or a value. A feature is on or off for the whole DMbot, not for this campaign.
import { featuresWords as w } from "../content/features-list-words";
import type { FeaturesList as Features } from "./api";

const when = (at: number): string => new Date(at * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

export default function FeaturesList({ features }: { features: Features }) {
  return (
    <section class="stack" aria-labelledby="features-heading" id="features">
      <h3 id="features-heading">{w.heading}</h3>
      {features.on.length === 0 ? (
        <p>{w.nothingOn}</p>
      ) : (
        <ul class="campaigns">
          {features.on.map((f) => (
            <li key={f.name}>
              <span class="campaign-name">
                <span aria-hidden="true">✅ </span>
                <span class="visually-hidden">{w.on}: </span>
                {f.name}
              </span>
              <span class="block">{f.words}</span>
            </li>
          ))}
        </ul>
      )}
      {features.off.length > 0 && features.on.length > 0 && (
        <>
          <h4>{w.offHeading}</h4>
          <ul class="stack">
            {features.off.map((name) => (
              <li key={name}>
                <span aria-hidden="true">⬜ </span>
                <span class="visually-hidden">{w.off}: </span>
                {name}
              </li>
            ))}
          </ul>
        </>
      )}
      <p class="small muted">{w.footer}</p>
      <p class="small muted">{w.asOf(when(features.writtenAt))}</p>
    </section>
  );
}

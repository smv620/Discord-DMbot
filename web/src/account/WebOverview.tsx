/** @jsxImportSource preact */
// What the Story graph hides when it draws one centre's web (#2632): how many names there are in all, the
// names with no connection yet (a list beside the map, not drawn, so it does not count against the map's
// size cap) and the other groups of linked names. The map alone drew 2 of a campaign's 51 names because
// it walks only the confirmed connections of its centre. The API decides what the person may see (a
// player gets no hidden name, a DM gets them flagged); this only lists what it sends.
import { kindWords, webPage as words } from "../content/web-words";
import type { WebOverview as Overview } from "./api";

export default function WebOverview({
  overview,
  center,
  alone,
  onCentre,
  onAlone,
}: {
  overview: Overview;
  /** The name now in the middle of the map. */
  center: string | null;
  /** A name from the tray that was picked, if any: it has no connection to draw. */
  alone: string | null;
  /** Put a group's name in the middle of the map. */
  onCentre: (id: string) => void;
  /** Pick a name that has no connection yet. */
  onAlone: (id: string | null) => void;
}) {
  const { counts, tray, groups } = overview;
  // The tray by kind, in the order the API sent them (kind, then name).
  const kinds: { key: string; names: Overview["tray"] }[] = [];
  for (const n of tray) {
    const last = kinds[kinds.length - 1];
    if (last && last.key === n.kind) last.names.push(n);
    else kinds.push({ key: n.kind, names: [n] });
  }
  const picked = tray.find((n) => n.id === alone);
  return (
    <section class="stack web-overview" aria-labelledby="web-overview-line">
      <p id="web-overview-line" role="status">
        <strong>{words.overviewLine(counts.names, counts.connections)}</strong>
      </p>
      {overview.truncated && <p class="note">{words.overviewTruncated}</p>}
      {groups.length > 1 && (
        <div class="stack">
          <h2 class="small">{words.groupsHeading}</h2>
          <p class="small muted">{words.groupsLead}</p>
          <ul class="row web-groups">
            {groups.map((g) => (
              <li key={g.center}>
                <button
                  type="button"
                  class="button secondary"
                  aria-current={g.center === center ? "true" : undefined}
                  onClick={() => {
                    onAlone(null);
                    onCentre(g.center);
                  }}
                >
                  {words.groupButton(g.centerName, g.size)}
                  {g.center === center && <span class="small muted"> ({words.groupHere})</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {tray.length > 0 && (
        <div class="stack" aria-labelledby="web-tray-heading" role="group">
          <h2 id="web-tray-heading" class="small">
            {words.trayHeading} ({tray.length})
          </h2>
          <p class="small muted">{words.trayLead}</p>
          {kinds.map((k) => (
            <div key={k.key} class="stack">
              <h3 class="small">{words.trayKind(kindWords(k.key), k.names.length)}</h3>
              <ul class="row web-tray">
                {k.names.map((n) => (
                  <li key={n.id} class={n.revealed === false ? "web-dim" : undefined}>
                    <button
                      type="button"
                      class="button secondary"
                      aria-pressed={n.id === alone}
                      onClick={() => onAlone(n.id === alone ? null : n.id)}
                    >
                      {n.secret && <span aria-hidden="true">{words.secretMark} </span>}
                      {n.name}
                      {n.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
                      {n.revealed === false && <span class="small muted"> ({words.notRevealed})</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      <p class="small" role="status">
        {picked ? words.noConnectionsYet(picked.name) : ""}
      </p>
    </section>
  );
}

/** @jsxImportSource preact */
// The "Pins and scale" panel under a map on the Maps page (#4672): the picture with DMbot's pins drawn on it, what DMbot knows of the
// map's scale, grid and north, and every mark with what it points to. Read only, DM only, behind MAP_PINS_LIVE. Paths and areas are
// listed, never drawn. Nothing here is shown to the players, and no id is ever shown.
import { useEffect, useState } from "preact/hooks";

import { mapPinsWords as w } from "../content/map-pins-words";
import { ApiError, type AccountApi, type MapFeatures, type MapItem } from "./api";

function sayTarget(f: MapFeatures["features"][number]): string {
  if (f.targetKind === null || f.target === null) return w.unmatched;
  if (f.targetKind === "place") return w.place(f.target);
  if (f.targetKind === "road") return w.road(f.target);
  return w.areaName(f.target);
}

export default function MapPins({ api, campaignRef, map }: { api: AccountApi; campaignRef: string; map: MapItem }) {
  const [data, setData] = useState<MapFeatures | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [picture, setPicture] = useState(true); // (a picture that cannot load hides; the lists still show)

  useEffect(() => {
    let live = true;
    if (!api.mapFeatures) return;
    api
      .mapFeatures(campaignRef, map.id)
      .then((got) => live && setData(got))
      .catch((error) => live && setProblem(error instanceof ApiError && error.kind === "not-allowed" ? w.notDm : w.failed));
    return () => {
      live = false;
    };
  }, [api, campaignRef, map.id]);

  if (problem) return <p class="warn" role="alert">{problem}</p>;
  if (!data) return <p class="muted" role="status">{w.loading}</p>;

  const pins = data.features.filter((f) => f.shape === "pin");
  const scale = data.scale === null ? w.noScale : w.scale(data.scale.unit, w.from[data.scale.from] ?? "");
  const grid =
    data.grid.kind === "none" ? null : data.grid.from ? w.grid(data.grid.kind, w.from[data.grid.from] ?? "") : `Grid: ${data.grid.kind}.`;
  const north = data.north.from === "assumed" ? w.northAssumed : w.north(data.north.angle, w.from[data.north.from] ?? "");

  return (
    <section class="stack" aria-labelledby={`pins-${map.id}`}>
      <h3 id={`pins-${map.id}`}>{w.heading}</h3>
      <p class="small">{w.privacy}</p>
      {picture && (
        <div style="position: relative; max-width: 100%">
          <img src={map.openUrl} alt={map.title} style="max-width: 100%; display: block" onError={() => setPicture(false)} />
          {pins.map((f) => (
            <span
              key={f.id}
              title={f.label}
              aria-label={f.label}
              style={`position: absolute; left: ${f.points[0]! * 100}%; top: ${f.points[1]! * 100}%; width: 0.7em; height: 0.7em; margin: -0.35em 0 0 -0.35em; border-radius: 50%; border: 2px solid currentColor; ${f.source === "dm" ? "background: currentColor;" : "background: transparent;"}`}
            />
          ))}
        </div>
      )}
      <p class="small">{scale}</p>
      {grid && <p class="small">{grid}</p>}
      <p class="small">{north}</p>
      <h4 class="small">{w.marks}</h4>
      {data.features.length === 0 ? (
        <p class="muted">{w.none}</p>
      ) : (
        <ul class="stack" aria-label={w.marks}>
          {data.features.map((f) => (
            <li key={f.id}>
              <p class="small">
                <strong>{`${f.label}: ${w.shape[f.shape] ?? ""}`}</strong>
              </p>
              <p class="small">{sayTarget(f)}</p>
              <p class="small">{w.source[f.source] ?? ""}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

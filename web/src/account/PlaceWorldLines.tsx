/** @jsxImportSource preact */
// What the table may be told of a place's land, climate and ruler (#4311), for anyone who may see the place: the same lines as Discord's
// card, from a door that gives only what the DM confirmed and showed, cut by the gate and the players' rule in the API. It is words only:
// no "(DMbot read this)", no group the reader may not know. Nothing is fetched until the reader presses the button.
import { useState } from "preact/hooks";

import { placeWorldWords as w } from "../content/place-world-words";
import type { AccountApi, NameRow, PlaceWorldTable } from "./api";

export default function PlaceWorldLines({ api, refId, row }: { api: AccountApi; refId: string; row: NameRow }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<PlaceWorldTable | null>(null);
  const [failed, setFailed] = useState(false);
  const id = `table-world-${row.id}`;

  async function toggle(): Promise<void> {
    const next = !open;
    setOpen(next);
    if (!next || data || !api.placeWorldTable) return;
    try {
      setData(await api.placeWorldTable(refId, row.id));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }

  const by = (layer: string) => data?.rows.find((r) => r.layer === layer && r.part === "");
  const land = by("terrain");
  const climate = by("climate");
  const rulers = data?.rows.filter((r) => r.layer === "control" && r.part === "") ?? [];
  const landWord = land ? (land.value === "other" ? land.words : (w.land.choices[land.value]?.[0] ?? land.value).toLowerCase()) : "";
  const warmWord = climate ? (w.climate.warmth[climate.value] ?? climate.value).toLowerCase() : "";
  return (
    <div class="small">
      <button type="button" class="link-button" aria-expanded={open} aria-controls={id} onClick={() => void toggle()}>
        {w.table.show}
      </button>
      {open && (
        <div id={id} class="stack" aria-live="polite">
          {failed ? (
            <p role="alert">{w.table.failed}</p>
          ) : !data ? (
            <p class="muted">{w.table.loading}</p>
          ) : !land && !climate && rulers.length === 0 ? (
            <p class="muted">{w.table.none}</p>
          ) : (
            <>
              {land && <p>{w.landLine(landWord, land.how === "partly")}</p>}
              {climate && <p>{w.climateLine(warmWord, climate.how)}</p>}
              {rulers.map((r) => (
                <p key={`${r.groupName}-${r.how}`}>{w.rulerLine(w.ruler.lines[r.how] ?? w.ruler.lines["rules"]!, r.groupName ?? "")}</p>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

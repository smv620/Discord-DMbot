/** @jsxImportSource preact */
// The Story Graph's settings (#3620): which kinds of name to show, the spacing for Spread out, the size of names and of
// their words, and whether a line grows with its connections. Every choice is the viewer's own, kept in this browser
// (webPrefs.ts) and never sent anywhere. Its own file so the page keeps its weight budget.
import { useState } from "preact/hooks";

import { graphSettingsWords as w } from "../content/graph-settings-words";
import { DEFAULT_LOOK, LOOK_RANGE, type GraphLook } from "./webPrefs";

export default function WebSettings({
  look,
  kinds,
  onChange,
}: {
  look: GraphLook;
  /** The kinds the campaign's names have, with how many of each. */
  kinds: { key: string; label: string; count: number }[];
  onChange: (next: GraphLook) => void;
}) {
  const [note, setNote] = useState("");
  const set = (part: Partial<GraphLook>): void => {
    setNote("");
    onChange({ ...look, ...part });
  };
  const slider = (id: string, label: string, low: string, high: string, key: "spacing" | "nodeSize" | "textSize", hint = "") => {
    const value = look[key];
    const said = value === 1 ? w.usual : w.percent(value);
    return (
      <div class="field">
        <label for={id}>
          {label}: <span>{said}</span>
        </label>
        {hint && (
          <p class="small muted" id={`${id}-hint`}>
            {hint}
          </p>
        )}
        <input
          id={id}
          type="range"
          style={{ width: "100%", minHeight: "2.75rem" }}
          min={LOOK_RANGE[key][0]}
          max={LOOK_RANGE[key][1]}
          step={0.1}
          value={value}
          aria-valuetext={said}
          aria-describedby={hint ? `${id}-hint` : undefined}
          // The spacing re-settles the whole map, so it is applied when the slider is let go; the sizes follow the thumb.
          {...(key === "spacing"
            ? { onChange: (e: Event) => set({ spacing: Number((e.target as HTMLInputElement).value) }) }
            : { onInput: (e: Event) => set({ [key]: Number((e.target as HTMLInputElement).value) }) })}
        />
        <div class="row small" style={{ justifyContent: "space-between" }} aria-hidden="true">
          <span>{low}</span>
          <span>{high}</span>
        </div>
      </div>
    );
  };
  return (
    <section class="panel stack" aria-labelledby="graph-settings-h">
      <h2 id="graph-settings-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      <fieldset class="stack">
        <legend>{w.kindsHeading}</legend>
        <p class="small muted">{w.kindsHint}</p>
        {kinds.map((k) => (
          <label key={k.key}>
            <input
              type="checkbox"
              checked={look.kinds.includes(k.key)}
              onChange={(e) => set({ kinds: (e.target as HTMLInputElement).checked ? [...look.kinds, k.key] : look.kinds.filter((x) => x !== k.key) })}
            />{" "}
            {w.kindCount(k.label, k.count)}
          </label>
        ))}
      </fieldset>
      {slider("graph-spacing", w.spacing, w.tighter, w.moreSpread, "spacing", w.spacingHint)}
      {slider("graph-node-size", w.nodeSize, w.smaller, w.bigger, "nodeSize")}
      {slider("graph-text-size", w.textSize, w.smaller, w.bigger, "textSize")}
      <label>
        <input type="checkbox" checked={look.lineWidth} aria-describedby="graph-line-hint" onChange={(e) => set({ lineWidth: (e.target as HTMLInputElement).checked })} /> {w.lineWidth}
      </label>
      <p class="small muted" id="graph-line-hint">{w.lineWidthHint}</p>
      <div class="row">
        <button
          type="button"
          class="button secondary"
          onClick={() => {
            onChange({ ...DEFAULT_LOOK });
            setNote(w.resetDone);
          }}
        >
          {w.reset}
        </button>
      </div>
      <p class="small" role="status">
        {note}
      </p>
    </section>
  );
}

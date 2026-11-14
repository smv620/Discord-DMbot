/** @jsxImportSource preact */
// The Names page's "Full campaign / Partial campaign" filter (#3675). Partial campaign lists the
// campaign's sessions and uploaded lore as ticks; the Names list then shows only names found in the
// ones ticked. The list of ticks is hidden under Full campaign and is read when first needed.
import { useState } from "preact/hooks";

import { sourceWords as words } from "../content/name-sources-words";
import type { AccountApi, NameSource } from "./api";

type Load = { kind: "idle" } | { kind: "loading" } | { kind: "error" } | { kind: "ready"; sources: NameSource[] };

export default function NameSources({
  api,
  campaignRef,
  picked,
  onPick,
  canEdit = false,
}: {
  api: AccountApi;
  campaignRef: string;
  /** The ids ticked; empty is the full campaign. */
  picked: string[];
  onPick: (ids: string[]) => void;
  /** The campaign's DMs: only they are offered the uploaded lore (its titles are theirs), players get sessions. */
  canEdit?: boolean;
}) {
  const [partial, setPartial] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  if (!api.nameSources) return null;
  const { nameSources } = api;

  const choose = (value: string) => {
    const wantPartial = value === "partial";
    setPartial(wantPartial);
    if (!wantPartial) {
      onPick([]); // back to the full campaign: nothing stays ticked behind the hidden list
      return;
    }
    if (load.kind !== "idle" && load.kind !== "error") return;
    setLoad({ kind: "loading" });
    nameSources(campaignRef).then(
      (sources) => setLoad({ kind: "ready", sources: canEdit ? sources : sources.filter((s) => s.kind === "session") }),
      () => setLoad({ kind: "error" }),
    );
  };

  const toggle = (id: string) => onPick(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  const group = (kind: NameSource["kind"], sources: NameSource[]) => sources.filter((s) => s.kind === kind);

  return (
    <div class="field">
      <label for="names-scope">{words.label}</label>
      <select id="names-scope" value={partial ? "partial" : "full"} onChange={(e) => choose((e.target as HTMLSelectElement).value)}>
        <option value="full">{words.full}</option>
        <option value="partial">{words.partial}</option>
      </select>
      {partial && load.kind === "loading" && (
        <p class="muted" role="status">
          {words.loading}
        </p>
      )}
      {partial && load.kind === "error" && (
        <p class="warn" role="alert">
          {words.failed}{" "}
          <button type="button" class="link-button" onClick={() => choose("partial")}>
            {words.tryAgain}
          </button>
        </p>
      )}
      {partial && load.kind === "ready" &&
        (load.sources.length === 0 ? (
          <p class="muted" role="status">
            {words.empty}
          </p>
        ) : (
          <fieldset class="names-types">
            <legend>{words.legend}</legend>
            <p class="small muted">{picked.length === 0 ? words.none : words.hint(picked.length)}</p>
            {picked.length > 0 && (
              <button type="button" class="link-button small" onClick={() => onPick([])}>
                {words.clear}
              </button>
            )}
            {(["session", "lore"] as const).map((kind) => {
              const items = group(kind, load.sources);
              return items.length === 0 ? null : (
                <div key={kind} class="names-type-list" role="group" aria-label={kind === "lore" ? words.lore : words.sessions}>
                  <p class="small">{kind === "lore" ? words.lore : words.sessions}</p>
                  {items.map((s) => (
                    <label class="choice names-type" key={s.id}>
                      <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} /> {s.label}
                    </label>
                  ))}
                </div>
              );
            })}
          </fieldset>
        ))}
    </div>
  );
}

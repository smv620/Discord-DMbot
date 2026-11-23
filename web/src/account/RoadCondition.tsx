/** @jsxImportSource preact */
// A road's or route place's condition (#4451, page half): the line, DMbot's read, and the DM's 🚧 Condition menu. Players see the line only.
// Drawn only behind ROAD_CONDITION_LIVE; with it off, nothing shows.
import { useEffect, useState } from "preact/hooks";

import { ROAD_CONDITION_LIVE } from "../content/road-condition-switch";
import { roadConditionWords as w } from "../content/road-condition-words";
import { ApiError, type AccountApi, type RoadCondition as Condition } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
type Choice = "open" | "hard" | "closed" | "gone" | "other" | "none";

export default function RoadCondition({
  api,
  refId,
  on,
  id,
  name,
  condition,
  road,
  canEdit,
  onChanged,
}: {
  api: AccountApi;
  refId: string;
  on: "connection" | "place";
  id: string;
  name: string;
  condition: Condition | undefined;
  road: boolean;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  // The last change's batch, and the line it replaced, so Undo puts that line back even after a re-read.
  const [undo, setUndo] = useState<{ batch: number | null; prev: Condition | undefined } | null>(null);
  // What the DM just set, shown until the graph is read again (the page reads it again after each change, #5035): `prev` is what it was before, for Undo.
  const [local, setLocal] = useState<{ cond: Condition | undefined; prev: Condition | undefined } | null>(null);
  useEffect(() => {
    setLocal(null);
  }, [condition]);
  if (!ROAD_CONDITION_LIVE) return null;

  const current = local ? local.cond : condition;
  // A player sees a connection's condition only once the DM has shown it (the server filters too).
  const playerSees = canEdit || on !== "connection" || current?.shown === true;
  const line = current?.value && playerSees ? w.line(current.value, current.words, current.since, current.before) : null;
  // The DM's view can set a road; a connection's read and show controls are for the connection only.
  const isDm = canEdit && road;
  const isConnection = on === "connection";

  async function run(action: () => Promise<void>, after?: () => void): Promise<void> {
    if (busy) return;
    setBusy(true);
    setSaid(null);
    try {
      await action();
      after?.();
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.gone : w.couldNot });
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  const choose = (value: Choice) =>
    run(
      async () => {
        if (!api.roadCondition) throw new ApiError("server");
        const before = current?.value ?? null;
        const made = await api.roadCondition(refId, { on, id, value, ...(value === "other" ? { words: words.trim() } : {}) });
        // Only "Something else" carries the DM's words; a change keeps the read and shown flags of what was there (the server keeps them too).
        const now =
          value === "none"
            ? undefined
            : {
                value: value as Condition["value"],
                words: value === "other" ? words.trim() : "",
                since: null,
                before: current?.value ? { value: current.value, words: current.words } : null,
                ...(current?.read !== undefined ? { read: current.read } : {}),
                ...(current?.shown !== undefined ? { shown: current.shown } : {}),
              };
        setLocal({ cond: now, prev: current });
        setUndo({ batch: made.batch, prev: current });
        setMenu(false);
        setWords("");
        setSaid({ ok: true, text: w.noted(name, value, words.trim(), before) });
      },
    );

  const keep = () =>
    run(async () => {
      if (!api.keepRoadCondition) throw new ApiError("server");
      await api.keepRoadCondition(refId, id);
    });

  const showHide = () =>
    run(async () => {
      if (!api.showRoadCondition) throw new ApiError("server");
      await api.showRoadCondition(refId, id, !current?.shown);
    });

  const takeBack = () =>
    run(
      async () => {
        if (!api.undoRouteLength || undo === null || undo.batch === null) throw new ApiError("server");
        await api.undoRouteLength(refId, undo!.batch!);
      },
      () => {
        const back = undo;
        setUndo(null);
        setLocal({ cond: back?.prev, prev: undefined });
      },
    );

  return (
    <div class="stack">
      {line && (
        <p class="small">
          {line}
          {canEdit && current?.read ? w.readNote : ""}
        </p>
      )}
      {isDm && (
        <div class="stack">
          <button type="button" class="button secondary" aria-expanded={menu} disabled={busy} onClick={() => setMenu(!menu)}>
            {w.button}
          </button>
          {menu && (
            <div class="stack" role="group" aria-label={w.menuPlaceholder}>
              <p class="small muted">{`How is ${name} now?`}</p>
              {w.choices.map((c) => (
                <div key={c.value} class="stack">
                  {c.value === "other" ? (
                    <div class="row">
                      <input
                        type="text"
                        value={words}
                        maxLength={60}
                        placeholder={c.hint}
                        disabled={busy}
                        onInput={(e) => setWords((e.target as HTMLInputElement).value)}
                      />
                      <button type="button" class="button secondary" disabled={busy || words.trim() === ""} onClick={() => void choose("other")}>
                        {c.label}
                      </button>
                    </div>
                  ) : (
                    <button type="button" class="button secondary" disabled={busy} onClick={() => void choose(c.value as Choice)}>
                      {c.label}: {c.hint}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          {isConnection && current?.read && (
            <button type="button" class="button secondary" disabled={busy} onClick={() => void keep()}>
              {w.conditionRight}
            </button>
          )}
          {isConnection && (
            <button type="button" class="button secondary" disabled={busy} onClick={() => void showHide()}>
              {current?.shown ? w.hide : w.show}
            </button>
          )}
        </div>
      )}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {undo !== null && undo.batch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void takeBack()}>
              {w.undoLabel}
            </button>
          )}
        </p>
      )}
      <SavingWait active={busy} />
    </div>
  );
}

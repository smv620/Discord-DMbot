/** @jsxImportSource preact */
// The name editor in the Story Graph's list (change the spelling or the kind of a name), and, for a DM, "Tavin now knows the
// guard is Ken" (#3542). A separate file, fetched only when a DM opens a name to change it, so the Story Graph page stays
// light (page weight, #1947).
import { useEffect, useRef, useState } from "preact/hooks";

import { NAME_LIMIT } from "../content/names-words";
import { TOLD_LIVE } from "../content/told-switch";
import { kindWords, webPage as words } from "../content/web-words";
import type { AccountApi, NameChange, WebNode } from "./api";
import LazyPanel from "./LazyPanel";
import SavingWait from "./SavingWait";

export default function WebNodePanel({ node, act, api, refId }: { node: WebNode; act: (id: string, change: NameChange, ok: string) => Promise<string | null>; api: AccountApi; refId: string }) {
  const [spelling, setSpelling] = useState(node.name);
  const [kind, setKind] = useState(node.kind);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); // a save is with the bot: no second press until it is answered
  const busyRef = useRef(false); // (a ref as well, so a quick second press before the redraw does nothing)
  const alive = useRef(true); // a save outlives the editor when the row closes on success: nothing is set after that
  useEffect(() => () => {
    alive.current = false;
  }, []);
  const kinds = Object.keys(words.kinds).includes(node.kind) ? Object.keys(words.kinds) : [node.kind, ...Object.keys(words.kinds)];
  async function save(change: NameChange): Promise<void> {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const refused = await act(node.id, change, words.saved);
      if (alive.current) setProblem(refused);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div class="stack web-node-editor" role="group" aria-label={words.nodePanel(node.name)}>
      <label class="field">
        {words.spellLabel}
        <input type="text" value={spelling} maxLength={NAME_LIMIT} onInput={(e) => setSpelling((e.target as HTMLInputElement).value)} />
      </label>
      <button type="button" class="button secondary" disabled={busy} onClick={() => void save({ action: "spell", name: spelling })}>
        {words.saveSpelling}
      </button>
      <label class="field">
        {words.kindChange}
        <select value={kind} onChange={(e) => setKind((e.target as HTMLSelectElement).value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {kindWords(k)}
            </option>
          ))}
        </select>
      </label>
      <button type="button" class="button secondary" disabled={busy} onClick={() => void save({ action: "kind", kind })}>
        {words.saveKind}
      </button>
      <SavingWait active={busy} />
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {TOLD_LIVE && api.toldPanel && (
        <LazyPanel load={() => import("./ToldPanel")} what="secret names" props={{ api, campaignRef: refId, entryId: node.id, entryName: node.name }} />
      )}
    </div>
  );
}

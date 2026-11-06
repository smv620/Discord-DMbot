/** @jsxImportSource preact */
// The name editor in the Story Graph's list (change the spelling or the kind of a name), and, for a DM, "Tavin now knows the
// guard is Ken" (#3542). A separate file, fetched only when a DM opens a name to change it, so the Story Graph page stays
// light (page weight, #1947).
import { useState } from "preact/hooks";

import { NAME_LIMIT } from "../content/names-words";
import { TOLD_LIVE } from "../content/told-switch";
import { kindWords, webPage as words } from "../content/web-words";
import type { AccountApi, NameChange, WebNode } from "./api";
import LazyPanel from "./LazyPanel";

export default function WebNodePanel({ node, act, api, refId }: { node: WebNode; act: (id: string, change: NameChange, ok: string) => Promise<string | null>; api: AccountApi; refId: string }) {
  const [spelling, setSpelling] = useState(node.name);
  const [kind, setKind] = useState(node.kind);
  const [problem, setProblem] = useState<string | null>(null);
  const kinds = Object.keys(words.kinds).includes(node.kind) ? Object.keys(words.kinds) : [node.kind, ...Object.keys(words.kinds)];
  return (
    <div class="stack" role="group" aria-label={words.nodePanel(node.name)}>
      <label class="field">
        {words.spellLabel}
        <input type="text" value={spelling} maxLength={NAME_LIMIT} onInput={(e) => setSpelling((e.target as HTMLInputElement).value)} />
      </label>
      <button
        type="button"
        class="button secondary"
        onClick={async () => setProblem(await act(node.id, { action: "spell", name: spelling }, words.saved))}
      >
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
      <button
        type="button"
        class="button secondary"
        onClick={async () => setProblem(await act(node.id, { action: "kind", kind }, words.saved))}
      >
        {words.saveKind}
      </button>
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

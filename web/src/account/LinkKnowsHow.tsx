/** @jsxImportSource preact */
// How the two people of a "knows" connection know each other, for the campaign's DM (#3882, website half of #3881; core
// #3889): "Yes, they've met", "No, only heard of them" or "Not said if they've met", beside the feeling picker. DMbot reads
// this out of the story by itself and never moves it down on its own: this is the DM's word. Its own file, loaded when a line
// is chosen. One save at a time; the choice follows the connection when it changes without this panel (Undo, Discord, a fresh read).
import { useEffect, useRef, useState } from "preact/hooks";

import { type KnowsHow, knowsHowWords as w } from "../content/knows-how-words";
import type { WebLinkChange } from "./api";

export default function LinkKnowsHow({
  n,
  id,
  how,
  act,
}: {
  n: number;
  id: string;
  how: KnowsHow;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
}) {
  const [picked, setPicked] = useState<KnowsHow>(how);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => {
    alive.current = false;
  }, []);
  const seen = useRef(how);
  useEffect(() => {
    if (seen.current === how) return;
    seen.current = how;
    setPicked(how);
  }, [how]);
  async function save(): Promise<void> {
    if (saving || picked === how) return;
    setSaving(true);
    try {
      const refused = await act({ action: "knows-how", id, how: picked }, w.saved);
      if (alive.current) setProblem(refused);
    } finally {
      if (alive.current) setSaving(false);
    }
  }
  return (
    <div class="stack">
      <label class="field">
        {w.label(n)}
        <select value={picked} onChange={(e) => setPicked((e.target as HTMLSelectElement).value as KnowsHow)}>
          {(["met", "aware", ""] as const).map((h) => (
            <option key={h || "not-said"} value={h}>
              {w.options[h]}
            </option>
          ))}
        </select>
      </label>
      <div class="row">
        <button type="button" class="button secondary" disabled={saving || picked === how} aria-label={w.saveFor(n)} onClick={() => void save()}>
          {saving ? w.saving : w.save}
        </button>
      </div>
      {problem && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/** @jsxImportSource preact */
// The feeling picker on a "knows (…)" connection in the Story Graph's line panel (#3607, website half of #3606): one of
// the nine words, saved as a feeling so the connection stays a "knows" (free words would turn it into a catch-all), and
// "Why <feeling>?" with where it came from and the words it was read from. For the campaign's DM only: the quote is
// never sent to, or drawn for, anyone else. Its own file so the Story Graph's page stays within its weight budget.
import { useEffect, useRef, useState } from "preact/hooks";

import { FEELINGS } from "../content/knows-feeling-words";
import { feelingWords as w } from "../content/knows-feeling-text";
import type { WebLink, WebLinkChange } from "./api";

function fromLine(source: WebLink["source"]): string {
  if (source?.type === "session" && source.n) return w.from.session(source.n);
  if (source?.type === "upload" && source.label) return w.from.upload(source.label, source.line);
  if (source?.type === "dm") return w.from.dm;
  return "";
}

export default function LinkFeeling({
  n,
  link,
  feeling,
  act,
}: {
  n: number;
  link: WebLink;
  feeling: string;
  act: (change: WebLinkChange, ok: string) => Promise<string | null>;
}) {
  const [picked, setPicked] = useState(feeling);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const from = fromLine(link.source);
  const alive = useRef(true); // a save outlives a press: the row may be gone when it ends
  useEffect(() => () => {
    alive.current = false;
  }, []);
  // The feeling changed without this panel (Undo, Discord, a fresh read): the choice follows it.
  useEffect(() => setPicked(feeling), [feeling]);
  async function save(): Promise<void> {
    if (saving || picked === feeling) return;
    setSaving(true);
    try {
      const refused = await act({ action: "feeling", id: link.id, feeling: picked }, w.saved);
      if (alive.current) setProblem(refused);
    } finally {
      if (alive.current) setSaving(false);
    }
  }
  return (
    <div class="stack">
      <label class="field">
        {w.label(n)}
        <select value={picked} onChange={(e) => setPicked((e.target as HTMLSelectElement).value)}>
          {FEELINGS.map((f) => (
            <option key={f} value={f}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </option>
          ))}
        </select>
      </label>
      <div class="row">
        <button type="button" class="button secondary" aria-label={w.saveFor(n)} aria-disabled={saving || picked === feeling} onClick={() => void save()}>
          {saving ? w.saving : w.save}
        </button>
        {picked !== feeling && <span class="small muted">{w.notSaved}</span>}
      </div>
      {problem && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
      <div class="stack small">
        <p>
          <strong>{w.why(feeling)}</strong> {from || w.noSource}
        </p>
        {link.quote ? (
          <>
            <p class="muted">{w.quoteCaption}</p>
            <blockquote>
              <span class="visually-hidden">{w.quoteLabel}: </span>
              {link.quote}
            </blockquote>
          </>
        ) : from ? (
          <p class="muted">{w.noQuote}</p>
        ) : null}
      </div>
    </div>
  );
}

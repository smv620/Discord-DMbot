/** @jsxImportSource preact */
// The Icon row on a player character's details panel (#3622): Use Discord, Use dndbeyond.com, Remove and Hide the icon,
// with one Undo. Offered only to the DM and to the player who plays the character (the door says which through
// `canSetIcon`, and checks again); a button is offered only when it would change something, and a picture only when it
// exists. Its own file so the Story Graph's page keeps its weight budget.
import { useRef, useState } from "preact/hooks";

import { pcIconWords as w } from "../content/pc-icon-words";
import type { IconChoice, WebNode } from "./api";

export default function PcIconRow({
  node,
  onChoose,
  undo,
  onUndo,
}: {
  node: WebNode;
  /** Sends the choice; resolves to a plain-words problem, or null when it was saved. */
  onChoose: (choice: IconChoice) => Promise<string | null>;
  /** Set when the last change here can be taken back. */
  undo: boolean;
  onUndo: () => Promise<string | null>;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const statusBox = useRef<HTMLParagraphElement>(null); // a pressed button may leave the row: focus goes to what was said
  const choice = node.iconChoice ?? "default";
  const has = node.iconHas ?? { discord: false, dndbeyond: false };
  async function go(run: () => Promise<string | null>, ok: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaid("");
    try {
      const refused = await run();
      if (refused) setProblem(refused);
      else {
        setSaid(ok);
        setTimeout(() => statusBox.current?.focus(), 0);
      }
    } finally {
      setBusy(false);
    }
  }
  const pick = (to: IconChoice) => () => void go(() => onChoose(to), w.done);
  return (
    <div class="stack" role="group" aria-labelledby={`pc-icon-${node.id}`}>
      <h4 id={`pc-icon-${node.id}`}>{w.heading}</h4>
      <p class="small muted">{w.scope}</p>
      <p class="small muted" aria-live="polite">
        {w.showing[choice]}
      </p>
      <div class="row">
        {has.discord && choice !== "discord" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("discord")}>
            {w.useDiscord}
          </button>
        )}
        {has.dndbeyond && choice !== "dndbeyond" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("dndbeyond")}>
            {w.useBeyond}
          </button>
        )}
        {choice !== "default" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("default")}>
            {w.remove}
          </button>
        )}
        {choice !== "hidden" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("hidden")}>
            {w.hide}
          </button>
        )}
        {undo && (
          <button type="button" class="button secondary" aria-label={w.undoLabel} aria-disabled={busy} onClick={() => void go(onUndo, w.undone)}>
            {w.undo}
          </button>
        )}
      </div>
      {choice !== "default" && <p class="small muted">{w.removeHint}</p>}
      <p class="small" role="status" tabIndex={-1} ref={statusBox}>
        {busy ? w.working : said}
      </p>
      {problem && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

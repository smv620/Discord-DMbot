/** @jsxImportSource preact */
// The parts of joining two names that only show when it is on (#4155): the "Joining X" banner, and the "Connect to another name" button
// with its Ctrl/⌘ hint. A separate file so the Story Graph page's first download stays within its budget.
import { pairingWords as w } from "../content/connection-pairing-words";

export default function WebPairing({ kind, name, onPair, id }: { kind: "banner" | "button"; name: string; onPair: (id: string) => void; id: string }) {
  if (kind === "banner")
    return (
      <p class="web-side note" role="status">
        {w.pickSecond(name)}{" "}
        <button type="button" class="button secondary" onClick={() => onPair(id)}>
          {w.cancel}
        </button>
      </p>
    );
  return (
    <>
      <button type="button" class="button secondary" onClick={() => onPair(id)}>
        {w.connectTo}
      </button>
      <p class="small muted">{w.ctrlHint}</p>
    </>
  );
}

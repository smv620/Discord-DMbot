/** @jsxImportSource preact */
// "Read it now" on a file of "Your adventure" (#3694 slice E, behind ADVENTURE_LIVE; core #5315): the DM sees what it will cost, in the
// card's own sentence from core, and says yes before anything is sent. The bot then starts the read (this page polls the start), and from
// then the file's own `read` in the list shows how far it is: the parent re-reads the list by itself while any file is reading, and this
// shows the running read's stage, how far, how long and when it last moved (the shared wait panel). A read never starts without the yes.
import { useEffect, useRef, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AccountApi, ApiError, type AdventureDocument } from "./api";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

const FIRST_MS = 500;
const EVERY_MS = 1500;
const LOOKS = 40;

/** The running read of one file: stage, how far, how long and when it last moved (the page's own clock for the first two). */
export function ReadingWait({ doc }: { doc: AdventureDocument }) {
  const read = doc.read;
  const running = read?.status === "reading";
  const seen = useObservedWait(running, `${read?.nextChunk ?? 0}|${read?.updatedAt ?? 0}`);
  if (!running || !read) return null;
  return (
    <WorkProgress
      stage={w.readingStage}
      done={read.nextChunk}
      total={read.totalChunks}
      unit={w.readingUnit}
      startedAt={seen.startedAt}
      updatedAt={seen.movedAt ?? undefined} // (the page's own clock for all three times: they never mix with the server's)
      state={seen.quiet ? "stalled" : "running"}
    />
  );
}

export default function AdventureReadNow({ api, refId, doc, disabled, onStarted }: { api: AccountApi; refId: string; doc: AdventureDocument; disabled: boolean; onStarted: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [said, setSaid] = useState("");
  const epoch = useRef(0);
  const sending = useRef(false);
  const statusBox = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    return () => {
      epoch.current += 1; // leaving the page ends the watching
      sending.current = false;
    };
  }, [api, refId]);

  async function start(): Promise<void> {
    if (!api.adventureStartRead || !api.adventureStarting || sending.current) return;
    sending.current = true;
    const mine = epoch.current;
    const here = (): boolean => mine === epoch.current;
    setAsking(false);
    setBusy(true);
    setProblem("");
    setSaid("");
    const end = (text: string, bad: boolean): void => {
      if (!here()) return;
      setBusy(false);
      if (bad) setProblem(text);
      else setSaid(text);
      onStarted(); // (the files are read again, whatever happened)
      setTimeout(() => statusBox.current?.focus(), 0);
    };
    try {
      let id: string;
      try {
        id = (await api.adventureStartRead(refId, doc.id)).read;
      } catch (error) {
        const kind = error instanceof ApiError ? error.kind : "network";
        if (here()) {
          setBusy(false);
          setProblem(w.readRefused[kind] ?? w.readRefused.server ?? "");
        }
        return;
      }
      let errors = 0;
      for (let i = 0; i < LOOKS; i += 1) {
        await new Promise((ok) => setTimeout(ok, i === 0 ? FIRST_MS : EVERY_MS));
        if (!here()) return;
        try {
          const got = await api.adventureStarting(refId, id);
          errors = 0;
          if (got.state === "done") return end(w.started, false);
          if (got.state === "failed") return end(w.readFailed[got.error] ?? w.readFailed.failed ?? "", true);
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "signed-out")) return end(w.readSlow, true);
          errors += 1;
          if (errors >= 3) return end(w.readSlow, true);
        }
      }
      end(w.readSlow, true);
    } finally {
      sending.current = false;
    }
  }

  if (!api.adventureStartRead || !api.adventureStarting) return null;
  // Only a file with a cost sentence can be read: none means no text, or already read.
  const canRead = Boolean(doc.estimate) && !doc.restored && doc.read?.status !== "reading";
  return (
    <div class="stack">
      {canRead && !asking && !busy && (
        <button type="button" class="button secondary" aria-label={`${w.readNow} ${doc.name}`} disabled={disabled} onClick={() => setAsking(true)}>
          {w.readNow}
        </button>
      )}
      {asking && (
        <div class="stack" role="group" aria-labelledby={`adv-read-${doc.id}`}>
          <p id={`adv-read-${doc.id}`}>
            {w.readAsk(doc.name)} {doc.estimate}
          </p>
          <div class="row">
            <button type="button" class="button" onClick={() => void start()}>
              {w.readYes}
            </button>
            <button type="button" class="button secondary" autoFocus onClick={() => setAsking(false)}>
              {w.readNo}
            </button>
          </div>
        </div>
      )}
      {busy && (
        <p class="muted" role="status">
          {w.starting}
        </p>
      )}
      {said && (
        <p role="status" tabIndex={-1} ref={statusBox}>
          {said}
        </p>
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

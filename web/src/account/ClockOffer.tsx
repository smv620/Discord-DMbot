// The offer to set the game clock to where an imported session ended (#3199, #3259). After a press it asks the
// server again every two seconds until the answer is no longer "offered": the import page stops its own polling
// once every session is final, so the offer keeps its own. Shown only while IMPORT_CLOCK_LIVE is true.
import { useEffect, useRef, useState } from "preact/hooks";

import { IMPORT_CLOCK_LIVE } from "../content/import-clock-switch";
import { clockWords as w } from "../content/import-words";
import { ApiError, type AccountApi, type ImportClock } from "./api";

const LOOK_EVERY_MS = 2000;
const GIVE_UP_MS = 30_000;

export default function ClockOffer({
  api,
  campaignRef,
  importId,
  file,
  clock,
}: {
  api: AccountApi;
  campaignRef: string;
  importId: string;
  file: string;
  clock: ImportClock | null;
}) {
  const [current, setCurrent] = useState<ImportClock | null>(clock);
  const [sending, setSending] = useState<"set" | "not-now" | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [stalled, setStalled] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const sentAt = useRef(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => setCurrent(clock), [clock]);

  // While a choice is being sent, count the seconds so the wait shows DMbot is still working.
  useEffect(() => {
    if (!sending) return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [sending]);

  // After a press, look again until the answer is no longer "offered", or give up after thirty seconds.
  useEffect(() => {
    if (!sending || !api.importProgress) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async (): Promise<void> => {
      try {
        const next = await api.importProgress!(campaignRef, importId);
        if (!alive.current) return;
        const mine = next.sessions.find((s) => s.file === file)?.clock;
        if (mine && mine.state !== "offered") {
          setCurrent(mine);
          setSending(null);
          return;
        }
      } catch {
        // A failed look is not the answer: keep asking until the time is up.
        if (!alive.current) return;
      }
      if (Date.now() - sentAt.current >= GIVE_UP_MS) {
        setStalled(true);
        setSending(null);
        return;
      }
      timer = setTimeout(() => void look(), LOOK_EVERY_MS);
    };
    timer = setTimeout(() => void look(), LOOK_EVERY_MS);
    return () => clearTimeout(timer);
  }, [sending, api, campaignRef, importId, file]);

  if (!IMPORT_CLOCK_LIVE || !api.importClock || !api.importProgress) return null;
  if (!current || current.state === "expired") return null;

  const press = async (choice: "set" | "not-now"): Promise<void> => {
    if (!api.importClock) return;
    setStalled(false);
    setProblem(null);
    setSeconds(0);
    sentAt.current = Date.now();
    setSending(choice);
    try {
      await api.importClock(campaignRef, importId, file, choice);
    } catch (err) {
      if (!alive.current) return;
      setSending(null);
      setProblem(err instanceof ApiError && err.kind === "not-allowed" ? w.notDm : w.error);
    }
  };

  if (current.state !== "offered") {
    const done =
      current.state === "set"
        ? w.set(current.label)
        : current.state === "dismissed"
          ? w.dismissed
          : current.state === "moved_on"
            ? w.movedOn
            : current.state === "split"
              ? w.split
              : null;
    return done ? <p class="small">{done}</p> : null;
  }

  return (
    <div class="stack small">
      <p>
        {w.endedOn} <strong>{current.label}</strong>. {w.question}
      </p>
      <div class="row">
        <button type="button" class="button" disabled={sending !== null} onClick={() => void press("set")}>
          {w.setIt}
        </button>
        <button type="button" class="button secondary" disabled={sending !== null} onClick={() => void press("not-now")}>
          {w.notNow}
        </button>
      </div>
      {sending && <p role="status" class="small muted">{w.setting(seconds)}</p>}
      {stalled && !sending && <p role="status" class="small muted">{w.stalled(GIVE_UP_MS / 1000)}</p>}
      {problem && <p class="note bad" role="alert">{problem}</p>}
    </div>
  );
}

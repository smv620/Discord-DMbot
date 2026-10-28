/** @jsxImportSource preact */
// "Fix this line" under a line of a session's Cleaned transcript (#2858; core #2882). A player fixes their own line
// and the DM decides; a DM's change is made at once. The box is prefilled with the line's words; the bot does the
// work, and this polls for the answer on the same timings as the character sheets (#2564). Off until FIX_LINE_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { ApiError, type AccountApi, type FixAnswer } from "./api";
// The refusals that end a wait (looking again will not mend them); a network blip does not end it (#2564).
const ENDS = ["not-allowed", "not-found", "signed-out"];
import { OFFLINE_AFTER_MS, POLL_MS, LATE_POLL_MS, GIVE_UP_MS } from "./settings-timing";
import { fixLineWords as w } from "../content/fix-line-words";
import WorkProgress from "./WorkProgress";
import { stageWords } from "../content/work-progress";

const MAX = 200;

export default function FixLine({
  api,
  campaignRef,
  number,
  line,
  startedMs,
  speaker,
  isDm,
  busy,
  setBusy,
}: {
  api: AccountApi;
  campaignRef: string;
  number: number;
  /** The line as the page shows it: its text, with the speaker label. */
  line: string;
  startedMs: number;
  speaker: string;
  isDm: boolean;
  /** One fix at a time on the page: the page owns the flag, so every button waits while one runs. The flag lives in
   * the transcript list, so a reload or a campaign switch can clear it while a fix is still queued. That is accepted
   * for now (#2884); the fix still goes through on the server. */
  busy: boolean;
  setBusy: (value: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(() => w.wordsOf(line));
  const [stage, setStage] = useState<string | null>(null);
  const [since, setSince] = useState<number | null>(null);
  // The last answer the bot gave while the fix is going: its progress, for the wait panel (#2564).
  const [last, setLast] = useState<FixAnswer | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const live = useRef(true);
  const shown = useRef(campaignRef + ":" + number);
  shown.current = campaignRef + ":" + number;
  useEffect(() => () => {
    live.current = false;
  }, []);

  const count = [...text].length;
  const empty = text.trim() === "";
  const tooLong = count > MAX;

  async function send(): Promise<void> {
    if (busy || empty || tooLong || !api.fixTranscriptLine || !api.fixAnswer) return;
    const asked = shown.current;
    const here = () => live.current && shown.current === asked;
    setBusy(true);
    setSaid(null);
    setStage(w.stage.waiting);
    setSince(Math.floor(Date.now() / 1000));
    setLast(null);
    try {
      const queued = await api.fixTranscriptLine(campaignRef, number, { speaker, startedMs }, text.trim());
      const started = Date.now();
      for (let i = 0; live.current; i += 1) {
        await new Promise((r) => setTimeout(r, Date.now() - started > OFFLINE_AFTER_MS ? LATE_POLL_MS : POLL_MS[Math.min(i, POLL_MS.length - 1)]));
        if (!live.current) return;
        const final = Date.now() - started > GIVE_UP_MS;
        let got: FixAnswer | null = null;
        try {
          got = await api.fixAnswer(campaignRef, number, queued.answer);
        } catch (error) {
          if (error instanceof ApiError && ENDS.includes(error.kind)) throw error;
        }
        if (got && here()) setLast(got);
        if (got?.state === "done") {
          if (!here()) return;
          setStage(w.stage.done);
          const undo = got.result === "changed" && got.undo;
          setSaid(
            got.result === "changed"
              ? `${w.changed}${undo ? w.undoNote : ""}`
              : w.sentToDm,
          );
          if (got.result === "changed") setOpen(false);
          return;
        }
        if (got?.state === "failed") {
          if (here()) setSaid(w.failures[got.reason ?? ""] ?? w.failures.failed ?? null);
          return;
        }
        if (got && here()) setStage(stageWords(w.stage, got.stage));
        if (final) {
          if (here()) setSaid(w.quiet);
          return;
        }
      }
    } catch (error) {
      if (!live.current || shown.current !== asked) return;
      // The API's kind is hyphenated, and fix kinds carry a "fix-" prefix ("fix-open" is the words' "open").
      const kind = error instanceof ApiError ? error.kind.replace(/^fix-/, "").replace(/-/g, "_") : "other";
      setSaid(w.refusals[kind] ?? w.failures[kind] ?? w.failures.failed ?? null);
    } finally {
      if (live.current) {
        setBusy(false);
        setStage(null);
        setSince(null);
      }
    }
  }

  const name = line.length > 40 ? line.slice(0, 40) : line;
  return (
    <div class="stack fix-line">
      {!open && (
        <button
          type="button"
          class="button secondary"
          disabled={busy}
          aria-label={w.buttonFor(name)}
          onClick={() => {
            setText(w.wordsOf(line));
            setOpen(true);
            setSaid(null);
          }}
        >
          {w.button}
        </button>
      )}
      {open && (
        <form
          class="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label>
            {w.label}
            <textarea value={text} onInput={(e) => setText((e.target as HTMLTextAreaElement).value)} />
          </label>
          <p class={tooLong ? "warn small" : "small muted"}>{w.counter(count)}</p>
          <p class="small muted">{isDm ? w.dmNote : w.playerNote}</p>
          <div class="row">
            <button type="submit" class="button" disabled={busy || empty || tooLong}>
              {w.send}
            </button>
            <button type="button" class="button secondary" onClick={() => setOpen(false)}>
              {w.cancel}
            </button>
          </div>
        </form>
      )}
      {busy && stage && since !== null && (
        <WorkProgress
          stage={stage}
          startedAt={since}
          updatedAt={last?.updatedAt ?? null}
          serverNow={last?.serverNow ?? null}
          state={last?.state === "stalled" ? "stalled" : "running"}
        />
      )}
      {said && (
        <p class={said === w.quiet || said === w.sentToDm || said.startsWith(w.changed) ? "ok small" : "warn small"} role="status">
          {said}
        </p>
      )}
    </div>
  );
}

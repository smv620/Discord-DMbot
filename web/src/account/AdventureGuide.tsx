/** @jsxImportSource preact */
// "Where is the table?" on "Your adventure" (#3694 slice F, behind ADVENTURE_LIVE; core #5317): Discord's 🧭 Guide me. The bot works out
// where the table seems to be in the DM's own book and what lies ahead; the page shows that message exactly as Discord does (it is the
// book's own words: the DM's page only, never kept or logged), and offers "I'm in another chapter" from the chapters it was given. After a
// pick it offers "Back to <the chapter it was on>" and "Back to DMbot's guess", which stand in for Discord's Undo. Every ask is queued for the
// bot and polled, showing its stage, how long and when it last moved. Shown only when Shared story is on Guide me and a file was read.
import { useEffect, useRef, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AccountApi, ApiError, type AdventureGuideAnswer } from "./api";
import WorkProgress from "./WorkProgress";

const FIRST_MS = 500;
const EVERY_MS = 1500;
const LOOKS = 60;

type Job = "read" | "pick" | "clear";

export default function AdventureGuide({ api, refId, document }: { api: AccountApi; refId: string; document: string }) {
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<{ stage: string; startedAt: number; updatedAt: number; serverNow: number } | null>(null);
  const [answer, setAnswer] = useState<AdventureGuideAnswer | null>(null);
  const [before, setBefore] = useState<{ id: string; name: string } | null>(null); // the chapter it was on before the last pick
  const [chosen, setChosen] = useState("");
  const [problem, setProblem] = useState("");
  const epoch = useRef(0);
  const sending = useRef(false);
  const textBox = useRef<HTMLParagraphElement>(null);
  const waitBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return () => {
      epoch.current += 1; // leaving the page, or another campaign or file, ends the watching
      sending.current = false;
    };
  }, [api, refId, document]);
  useEffect(() => {
    if (busy) waitBox.current?.focus();
  }, [busy]);

  async function run(job: Job, chapter?: string, previous?: AdventureGuideAnswer | null): Promise<void> {
    if (!api.adventureGuide || !api.adventureGuiding || sending.current) return;
    sending.current = true;
    const mine = epoch.current;
    const here = (): boolean => mine === epoch.current;
    setShown(true);
    setBusy(true);
    setProblem("");
    setSeen(null);
    const stop = (text: string): void => {
      if (!here()) return;
      setBusy(false);
      setProblem(text);
    };
    try {
      let id: string;
      try {
        id = (await api.adventureGuide(refId, document, job, chapter)).guide;
      } catch (error) {
        return stop(w.guideRefused[error instanceof ApiError ? error.kind : "network"] ?? w.guideRefused.server ?? "");
      }
      let errors = 0;
      for (let i = 0; i < LOOKS; i += 1) {
        await new Promise((ok) => setTimeout(ok, i === 0 ? FIRST_MS : EVERY_MS));
        if (!here()) return;
        try {
          const got = await api.adventureGuiding(refId, id);
          errors = 0;
          if (!here()) return;
          setSeen({ stage: got.state, startedAt: got.createdAt, updatedAt: got.updatedAt, serverNow: got.serverNow });
          if (got.state === "failed") return stop(w.guideFailed[got.error] ?? w.guideFailed.failed ?? "");
          if (got.state === "done" && got.answer) {
            setBusy(false);
            // After a pick, "back" goes to the chapter it was on (when the DM had named one); a clear goes back to DMbot's own guess.
            setBefore(job === "pick" && previous?.picked && previous.chapter ? previous.chapter : null);
            setAnswer(got.answer);
            setChosen("");
            setTimeout(() => textBox.current?.focus(), 0);
            return;
          }
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "signed-out")) return stop(w.guideSlow);
          errors += 1;
          if (errors >= 3) return stop(w.guideSlow);
        }
      }
      stop(w.guideSlow);
    } finally {
      sending.current = false;
    }
  }

  if (!api.adventureGuide || !api.adventureGuiding) return null;
  return (
    <div class="stack">
      {!shown && (
        <button type="button" class="button secondary" onClick={() => void run("read")}>
          {w.guide}
        </button>
      )}
      {busy && (
        <div ref={waitBox} tabIndex={-1}>
          <WorkProgress
            stage={w.guideStages[seen?.stage ?? "waiting"] ?? w.guideWorking}
            startedAt={seen?.startedAt}
            updatedAt={seen?.updatedAt}
            serverNow={seen?.serverNow}
            state="running"
          />
        </div>
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {answer && !busy && (
        <div class="stack">
          <p class="small muted">{w.guideFrom}</p>
          {/* Discord's own message, exactly as it is (the book's own words): never put into an address, a log or storage. */}
          <p tabIndex={-1} ref={textBox} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {answer.text}
          </p>
          {answer.chapters.length > 0 && (
            <div class="stack">
              <label class="field" for={`adv-guide-${document}`}>
                {w.guideAnother}
              </label>
              <select id={`adv-guide-${document}`} value={chosen} onChange={(e) => setChosen((e.target as HTMLSelectElement).value)}>
                <option value="">{w.guideWhich}</option>
                {answer.chapters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <div class="row">
                <button type="button" class="button" disabled={!chosen} onClick={() => void run("pick", chosen, answer)}>
                  {w.guideUse}
                </button>
                {before && (
                  <button type="button" class="button secondary" onClick={() => void run("pick", before.id, answer)}>
                    {w.guideBackTo(before.name)}
                  </button>
                )}
                {answer.picked && (
                  <button type="button" class="button secondary" onClick={() => void run("clear")}>
                    {w.guideBackGuess}
                  </button>
                )}
              </div>
            </div>
          )}
          <button type="button" class="button secondary" onClick={() => {
              setShown(false);
              setAnswer(null);
              setBefore(null);
            }}>
            {w.guideHide}
          </button>
        </div>
      )}
    </div>
  );
}

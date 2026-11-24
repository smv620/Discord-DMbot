/** @jsxImportSource preact */
// "📝 More names" under the Questions page's "More names from your sessions" line (#5081, the web half of #5055 B): one
// button for a DM that turns the names an import left waiting into names to check, as Discord's does, and says the same
// words after. The press is queued for the bot, so the wait shows its stage, how long it has run and when it last moved
// (CLAUDE.md, "Every wait shows that DMbot is still working"). Counts only: no name from a transcript is shown here.
import { useRef, useState } from "preact/hooks";

import { moreNamesWords as w } from "../content/more-names-words";
import { type AccountApi, ApiError } from "./api";
import SavingWait from "./SavingWait";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

export default function MoreNames({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<{ done: number; total: number } | null>(null);
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState("");
  const inFlight = useRef(false);
  // The bot sends counts but no times, so the page notes when the wait began and when the count last moved (its own clock).
  const observed = useObservedWait(busy, seen === null ? "" : `${seen.done}/${seen.total}`);
  if (!api.moreNames) return null;
  const press = async (): Promise<void> => {
    if (inFlight.current || !api.moreNames) return;
    inFlight.current = true;
    setBusy(true);
    setSeen(null);
    setSaid("");
    setProblem("");
    try {
      const got = await api.moreNames(campaignRef, (progress) => setSeen(progress));
      setSaid(w.result(got.made, got.left));
    } catch (error) {
      // (the Questions list shows this kind to the campaign's game masters only, so a refusal here is a rare race: said plainly)
      const kind = error instanceof ApiError ? error.kind : "";
      setProblem(kind === "not-found" || kind === "not-allowed" || kind === "only-dm" ? w.notAllowed : kind === "too-soon" ? w.tooSoon : w.failed);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <div class="stack">
      <p class="small muted">{w.lead}</p>
      <button type="button" class="button secondary" disabled={busy} onClick={() => void press()}>
        {w.button}
      </button>
      {busy && seen === null && <SavingWait active stage={w.working} whatToDo={w.slow} />}
      {busy && seen !== null && observed.startedAt !== null && (
        <WorkProgress
          stage={w.working}
          done={seen.done}
          total={seen.total}
          unit={w.unit}
          startedAt={observed.startedAt}
          updatedAt={observed.movedAt}
          state={observed.quiet ? "stalled" : "running"}
          whatToDo={w.slow}
        />
      )}
      <p class="small" role="status" aria-live="polite">
        {said}
        {said !== "" && ` ${w.countLater}`}
      </p>
      {problem !== "" && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

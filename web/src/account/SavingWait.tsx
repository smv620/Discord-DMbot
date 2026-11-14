/** @jsxImportSource preact */
// The wait after a change that DMbot has to apply (a name edit, a merge, a spelling, a kind) when the page has no
// progress from the bot to show (#3347; owner rule 2026-10-23, "every wait shows DMbot is still working"): the stage in
// plain words, how long it has run, and, after a minute with no answer, that it has stalled and what to do. The page
// has no sign of the bot's progress here, so the "last moved" line is left out (`progressUnknown`) rather than made
// up. The stalled line comes a minute after the page first noticed the wait, whatever the bot does: the page cannot see
// more than that here, and says no more than "slow". One small component any page can drop in where it used to print
// "Saving…" beside a busy button, with `active` the page's own busy flag. Put it below the row of buttons, not inside a
// flex row. A page that already has the bot's own start time and progress should use WorkProgress directly.
import { savingWait as w } from "../content/work-progress";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

export default function SavingWait({ active, stage = w.stage, whatToDo = w.quiet }: { active: boolean; stage?: string; whatToDo?: string }) {
  const seen = useObservedWait(active, "");
  // The status region is always on the page, and only its contents come and go: a live region that appears already
  // filled is often not read out, and the stalled line is the one that matters most.
  return (
    <div role="status" aria-live="polite">
      {active && seen.startedAt !== null && (
        <WorkProgress stage={stage} startedAt={seen.startedAt} updatedAt={seen.movedAt} state={seen.quiet ? "stalled" : "running"} progressUnknown whatToDo={whatToDo} />
      )}
    </div>
  );
}

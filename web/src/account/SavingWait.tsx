/** @jsxImportSource preact */
// The wait after a change that DMbot has to apply (a name edit, a merge, a spelling, a kind) when the page has no
// progress from the bot to show (#3347; owner rule 2026-10-23, "every wait shows DMbot is still working"): the stage in
// plain words, how long it has run, and, after a minute with no answer, that it has stalled and what to do. The page
// has no sign of the bot's progress here, so the "last moved" line is left out (`progressUnknown`) rather than made
// up. One small component any page can drop in where it used to print "Saving…" beside a busy button.
import { savingWait as w } from "../content/work-progress";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

export default function SavingWait({ active, stage = w.stage, whatToDo = w.quiet }: { active: boolean; stage?: string; whatToDo?: string }) {
  const seen = useObservedWait(active, "");
  if (!active || seen.startedAt === null) return null;
  return <WorkProgress stage={stage} startedAt={seen.startedAt} updatedAt={seen.movedAt} state={seen.quiet ? "stalled" : "running"} progressUnknown whatToDo={whatToDo} />;
}

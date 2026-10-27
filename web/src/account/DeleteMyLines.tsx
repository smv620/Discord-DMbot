/** @jsxImportSource preact */
// "Delete my transcripts" for one server on My Account (#2769; core web/my_lines.py): the person's own lines in that
// server, the same steps and words as Discord's 🗑 button: one short warning with the counts, one confirmation. The
// bot does the deleting (it holds a running session's saves back so nothing is written again), so the page queues it
// and waits for the answer with the stage, how long and when it last moved (#2564). Loaded only when the person asks
// (LazyPanel in the recording row) and shown only while DELETE_MY_LINES_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { deleteMyLinesWords as w } from "../content/delete-my-lines-words";
import { stageWords } from "../content/work-progress";
import { ApiError, type AccountApi, type MyLines, type MyLinesStatus } from "./api";
import { GIVE_UP_MS, LATE_POLL_MS, OFFLINE_AFTER_MS, POLL_MS } from "./settings-timing";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

type Phase =
  | { kind: "idle" }
  | { kind: "looking" }
  | { kind: "asking"; lines: number; sessions: number }
  | { kind: "working"; seen: "waiting" | "deleting" }
  | { kind: "gaveup" }
  | { kind: "done"; text: string }
  | { kind: "error"; message: string };

const problem = (kind: string): string => (Object.hasOwn(w.problem, kind) ? (w.problem[kind] as string) : (w.problem["server"] as string));
const ENDS = ["not-found", "signed-out"]; // looking again will not mend these

export default function DeleteMyLines({ api, serverId, serverName }: { api: AccountApi; serverId: string; serverName: string }) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const working = phase.kind === "working";
  const seen = useObservedWait(working, working ? phase.seen : "");
  const set = (next: Phase) => {
    if (live.current) setPhase(next);
  };

  /** Follow a request until the bot has answered, or until the hour the page waits is up. */
  async function follow(id: string) {
    if (!api.myLinesStatus) return set({ kind: "error", message: problem("server") });
    set({ kind: "working", seen: "waiting" });
    const started = Date.now();
    for (let i = 0; live.current; i += 1) {
      const behind = Date.now() - started > OFFLINE_AFTER_MS;
      await new Promise((r) => setTimeout(r, behind ? LATE_POLL_MS : POLL_MS[Math.min(i, POLL_MS.length - 1)]));
      if (!live.current) return;
      const final = Date.now() - started > GIVE_UP_MS;
      let got: MyLinesStatus;
      try {
        got = await api.myLinesStatus(serverId, id);
      } catch (error) {
        if (error instanceof ApiError && ENDS.includes(error.kind)) return set({ kind: "error", message: problem(error.kind) });
        if (final) return set({ kind: "gaveup" });
        continue; // a look that fails is tried again until the hour is up
      }
      if (got.state === "done") return set({ kind: "done", text: w.done(serverName, got.lines ?? 0) });
      if (got.state === "failed") return set({ kind: "error", message: w.failed });
      if (final) return set({ kind: "gaveup" });
      set({ kind: "working", seen: got.stage === "deleting" ? "deleting" : "waiting" });
    }
  }

  async function look() {
    if (!api.myLines) return set({ kind: "error", message: problem("server") });
    set({ kind: "looking" });
    let mine: MyLines;
    try {
      mine = await api.myLines(serverId);
    } catch (error) {
      return set({ kind: "error", message: problem(error instanceof ApiError ? error.kind : "server") });
    }
    if (mine.open) return void follow(mine.open.id); // one is already on its way: show it
    if (mine.lines === 0) return set({ kind: "done", text: w.nothing(serverName) });
    set({ kind: "asking", lines: mine.lines, sessions: mine.sessions });
  }

  async function ask() {
    if (!api.deleteMyLines) return set({ kind: "error", message: problem("server") });
    let id: string;
    try {
      id = await api.deleteMyLines(serverId);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "nothing-to-delete") return set({ kind: "done", text: w.nothing(serverName) });
      return set({ kind: "error", message: problem(error instanceof ApiError ? error.kind : "server") });
    }
    void follow(id);
  }

  return (
    <div class="stack" data-delete-lines={serverId}>
      {(phase.kind === "idle" || phase.kind === "error" || phase.kind === "done" || phase.kind === "gaveup") && (
        <button type="button" class="button secondary" aria-label={w.buttonFor(serverName)} onClick={() => void look()}>
          {w.button}
        </button>
      )}
      {phase.kind === "looking" && (
        <p class="muted small" role="status">
          {w.looking}
        </p>
      )}
      {phase.kind === "asking" && (
        <div class="stack" role="group" aria-label={w.buttonFor(serverName)}>
          <p role="alert">{w.warning(serverName, phase.lines, phase.sessions)}</p>
          <div class="row">
            {/* First, so the safe choice comes before the one that cannot be undone. */}
            <button type="button" class="button secondary" onClick={() => set({ kind: "idle" })}>
              {w.cancel}
            </button>
            <button type="button" class="button" onClick={() => void ask()}>
              {w.yes}
            </button>
          </div>
        </div>
      )}
      {phase.kind === "working" && seen.startedAt !== null && (
        <WorkProgress stage={stageWords(w.stage, phase.seen)} startedAt={seen.startedAt} updatedAt={seen.movedAt} state={seen.quiet ? "stalled" : "running"} whatToDo={w.quiet} />
      )}
      {phase.kind === "gaveup" && <WorkProgress stage={stageWords(w.stage, "waiting")} state="stalled" frozen whatToDo={w.gaveUp} />}
      <p class="small" role="status">
        {phase.kind === "done" ? phase.text : ""}
      </p>
      <p class="warn small" role="alert">
        {phase.kind === "error" ? phase.message : ""}
      </p>
    </div>
  );
}

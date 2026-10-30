/** @jsxImportSource preact */
// Pause / Unpause on a campaign's page, for its owner (#2985): the choice is queued for the bot like the other
// settings, the page follows the answer with the shared wait panel, and the bot's own words come back if the plan has
// no room to unpause. A game master who is not the owner never sees this (the page shows the state and says to ask).
import { useEffect, useRef, useState } from "preact/hooks";

import { pausePanel as words } from "../content/pause-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

type Wait = { on: boolean; state: CampaignSettingAnswer["state"] | "sending" | "timed_out"; note: string; error: string; since: number };

export default function PausePanel({ api, campaignRef, paused, onChanged }: { api: AccountApi; campaignRef: string; paused: boolean; onChanged?: (paused: boolean) => void }) {
  const [isPaused, setPaused] = useState(paused);
  const [asking, setAsking] = useState(false);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problem, setProblem] = useState("");
  const live = useRef(true);
  const yes = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => () => void (live.current = false), []);
  // The button that was pressed goes away to make room for the question: keyboard focus moves to the question's first button,
  // and back to the first button when the question is closed.
  useEffect(() => {
    if (asking) yes.current?.focus();
  }, [asking]);

  const go = async (): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    const on = !isPaused;
    setAsking(false);
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ on, state: "sending", note: "", error: "", since });
    let queued = false;
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "pause", value: "", on });
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ on, state: got.state, note: got.note, error: got.error, since });
        if (got.state === "done") {
          setPaused(on);
          onChanged?.(on);
          return;
        }
        if (got.state === "failed") return;
      }
      // The answer never came: say so and give the button back, never a panel that runs for ever.
      if (live.current) setWait({ on, state: "timed_out", note: "", error: "", since });
    } catch (err) {
      if (queued) {
        // It was sent; only the look at the answer failed. Do not say it failed: it may well have worked.
        setWait({ on, state: "timed_out", note: "", error: "", since });
        return;
      }
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    }
  };

  const kind = wait?.on ? "pause" : "unpause";
  return (
    <div class="stack">
      <p>{isPaused ? words.pausedNow : words.runningNow}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.timedOut}</p>}
      {wait && wait.state !== "timed_out" && (
        <WorkProgress
          stage={wait.state === "done" ? words.done[kind] : wait.state === "working" ? words.working[kind] : words.waiting[kind]}
          startedAt={wait.since}
          state={wait.state === "failed" ? "failed" : wait.state === "done" ? "done" : "running"}
          reason={wait.state === "failed" ? `${wait.note || words.failed} ${isPaused ? words.stillPaused : words.stillRunning}${wait.on ? "" : ` ${words.noRoomNext}`}` : null}
        />
      )}
      {!asking && (
        <p>
          <button type="button" class="button secondary" ref={first} disabled={wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out"} onClick={() => setAsking(true)}>
            {isPaused ? words.unpauseButton : words.pauseButton}
          </button>
        </p>
      )}
      {asking && (
        <div class="stack" role="group" aria-label={words.confirmLabel}>
          <p>{isPaused ? words.unpauseAsk : words.pauseAsk}</p>
          <p>
            <button type="button" class="button" ref={yes} onClick={() => void go()}>
              {isPaused ? words.yesUnpause : words.yesPause}
            </button>{" "}
            <button type="button" class="button secondary" onClick={() => { setAsking(false); setTimeout(() => first.current?.focus(), 0); }}>
              {words.keep}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

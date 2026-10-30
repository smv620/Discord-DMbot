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

type Wait = { on: boolean; state: CampaignSettingAnswer["state"] | "sending"; note: string; error: string; since: number };

export default function PausePanel({ api, campaignRef, paused }: { api: AccountApi; campaignRef: string; paused: boolean }) {
  const [isPaused, setPaused] = useState(paused);
  const [asking, setAsking] = useState(false);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problem, setProblem] = useState("");
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  const go = async (): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    const on = !isPaused;
    setAsking(false);
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ on, state: "sending", note: "", error: "", since });
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "pause", value: "", on });
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ on, state: got.state, note: got.note, error: got.error, since });
        if (got.state === "done") return void setPaused(on);
        if (got.state === "failed") return;
      }
    } catch (err) {
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    }
  };

  const kind = wait?.on ? "pause" : "unpause";
  return (
    <div class="stack">
      <p>{isPaused ? words.pausedNow : words.runningNow}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && (
        <WorkProgress
          stage={wait.state === "done" ? words.done[kind] : wait.state === "working" ? words.working[kind] : words.waiting[kind]}
          startedAt={wait.since}
          state={wait.state === "failed" ? "failed" : wait.state === "done" ? "done" : "running"}
          reason={wait.state === "failed" ? wait.note || words.failed : null}
        />
      )}
      {!asking && (
        <p>
          <button type="button" class="button secondary" disabled={wait !== null && wait.state !== "done" && wait.state !== "failed"} onClick={() => setAsking(true)}>
            {isPaused ? words.unpauseButton : words.pauseButton}
          </button>
        </p>
      )}
      {asking && (
        <div class="stack" role="group" aria-label={isPaused ? words.unpauseAsk : words.pauseAsk}>
          <p>{isPaused ? words.unpauseAsk : words.pauseAsk}</p>
          <p>
            <button type="button" class="button" onClick={() => void go()}>
              {isPaused ? words.yesUnpause : words.yesPause}
            </button>{" "}
            <button type="button" class="button secondary" onClick={() => setAsking(false)}>
              {words.keep}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

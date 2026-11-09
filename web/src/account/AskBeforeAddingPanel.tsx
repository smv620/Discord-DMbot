/** @jsxImportSource preact */
// "Ask me before adding" on a campaign's settings (#3879, the website's twin of the Manage DMbot card's setting; core #3887):
// on, DMbot asks before it adds what an upload found; off (the default), it adds what it is sure of by itself and says so. The
// choice is queued for the bot like the other settings and the page follows the answer with the shared wait panel. Copied
// from ShareStoryPanel without its question: turning it either way is safe (an upload can be undone for 7 days), so it asks nothing.
import { useEffect, useRef, useState } from "preact/hooks";

import { autoAddWords as words } from "../content/auto-add-words";
import { shareStoryWords as shared } from "../content/share-story-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer, type JobProgress } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

type Wait = { on: boolean; state: CampaignSettingAnswer["state"] | "sending" | "timed_out"; since: number; progress?: JobProgress | null | undefined };

export default function AskBeforeAddingPanel({ api, campaignRef, on }: { api: AccountApi; campaignRef: string; on: boolean }) {
  const [isOn, setOn] = useState(on);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problem, setProblem] = useState("");
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  const go = async (next: boolean): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ on: next, state: "sending", since });
    let queued = false;
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "ask_before_adding", value: next ? "on" : "off" });
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ on: next, state: got.state, since, progress: got.progress });
        if (got.state === "done") {
          setOn(next);
          return;
        }
        if (got.state === "failed") return;
      }
      if (live.current) setWait({ on: next, state: "timed_out", since });
    } catch (err) {
      if (queued) {
        setWait({ on: next, state: "timed_out", since });
        return;
      }
      setWait(null);
      setProblem(shared.problems[err instanceof ApiError ? err.kind : ""] ?? shared.failed);
    }
  };

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";
  return (
    <div class="stack">
      <h3>{words.setting.heading}</h3>
      <p>{isOn ? words.setting.on : words.setting.off}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{shared.quiet}</p>}
      {wait && wait.state === "done" && <p role="status">{words.setting.saved}</p>}
      {wait && wait.state !== "timed_out" && wait.state !== "done" && (
        <WorkProgress
          stage={wait.state === "working" ? shared.working : shared.waiting}
          startedAt={wait.progress?.startedAt ?? wait.since}
          updatedAt={wait.progress?.updatedAt}
          serverNow={wait.progress?.serverNow}
          state={wait.progress?.state ?? (wait.state === "failed" ? "failed" : "running")}
          reason={wait.state === "failed" ? shared.failed : null}
        />
      )}
      <p>
        <button type="button" class="button secondary" disabled={busy} onClick={() => void go(!isOn)}>
          {isOn ? words.setting.toggleOff : words.setting.toggleOn}
        </button>
      </p>
    </div>
  );
}

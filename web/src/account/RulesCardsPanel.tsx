/** @jsxImportSource preact */
// The website's "Rules cards" switch on a campaign's Settings (#5155): the game master's choice is queued for the bot like
// the other settings, and the page follows the answer with the shared wait panel. A copy of MapChecksPanel for its setting.
import { useEffect, useRef, useState } from "preact/hooks";

import { rulesCardsWords as words } from "../content/rules-cards-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer, type JobProgress } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

type Wait = {
  on: boolean;
  state: CampaignSettingAnswer["state"] | "sending" | "timed_out";
  since: number;
  progress?: JobProgress | null | undefined;
};

export default function RulesCardsPanel({ api, campaignRef, on }: { api: AccountApi; campaignRef: string; on: boolean }) {
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
      const id = await api.setCampaignSetting(campaignRef, { setting: "rules_cards", value: next ? "on" : "off" });
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
        // It was sent; only the look at the answer failed. Do not say it failed: it may well have worked.
        setWait({ on: next, state: "timed_out", since });
        return;
      }
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.failed);
    }
  };

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";
  const stage = wait?.state === "working" ? words.working : words.waiting;
  return (
    <div class="stack">
      <h3>{words.heading}</h3>
      <p>{words.lead}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.quiet}</p>}
      {wait && wait.state === "done" && <p role="status">{words.saved}</p>}
      {wait && wait.state !== "timed_out" && wait.state !== "done" && (
        <WorkProgress
          stage={stage}
          startedAt={wait.progress?.startedAt ?? wait.since}
          updatedAt={wait.progress?.updatedAt}
          serverNow={wait.progress?.serverNow}
          state={wait.progress?.state ?? (wait.state === "failed" ? "failed" : "running")}
          reason={wait.state === "failed" ? words.failed : null}
        />
      )}
      <p>
        <button
          type="button"
          class="button secondary"
          aria-pressed={isOn}
          disabled={busy}
          onClick={() => void go(!isOn)}
        >
          {isOn ? words.on : words.off}
        </button>
      </p>
    </div>
  );
}

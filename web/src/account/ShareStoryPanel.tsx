/** @jsxImportSource preact */
// "Share this campaign's story to improve DMbot" on a campaign's settings (#3741): the game master's choice is queued for
// the bot like the other settings, and the page follows the answer with the shared wait panel. Copied from PausePanel:
// the question comes only when turning sharing on; turning it off asks nothing.
import { useEffect, useRef, useState } from "preact/hooks";

import { shareStoryWords as words } from "../content/share-story-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer, type JobProgress } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

type Wait = {
  on: boolean;
  state: CampaignSettingAnswer["state"] | "sending" | "timed_out";
  since: number;
  /** The API's own times and state for the wait (#3355); absent from an older server, then the page's clock is used. */
  progress?: JobProgress | null | undefined;
};

export default function ShareStoryPanel({ api, campaignRef, on }: { api: AccountApi; campaignRef: string; on: boolean }) {
  const [isOn, setOn] = useState(on);
  const [asking, setAsking] = useState(false);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problem, setProblem] = useState("");
  const live = useRef(true);
  const yes = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => () => void (live.current = false), []);
  // The button that was pressed goes away to make room for the question: focus moves to the question's first button.
  useEffect(() => {
    if (asking) yes.current?.focus();
  }, [asking]);

  const go = async (next: boolean): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    setAsking(false);
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ on: next, state: "sending", since });
    let queued = false;
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "share_story", value: next ? "on" : "off" });
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
      // The answer never came: say so and give the button back, never a panel that runs for ever.
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
  const stage = wait?.state === "done" ? (wait.on ? words.doneOn : words.doneOff) : wait?.state === "working" ? words.working : words.waiting;
  return (
    <div class="stack">
      <h3>{words.heading}</h3>
      <p>{words.lead}</p>
      <p>{isOn ? words.onState : words.offState}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.quiet}</p>}
      {/* The done line is the panel's own words: the shared wait panel would show its generic "Done" instead. */}
      {wait && wait.state === "done" && <p role="status">{wait.on ? words.doneOn : words.doneOff}</p>}
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
      {!asking && (
        <p>
          <button
            type="button"
            class="button secondary"
            ref={first}
            disabled={busy}
            onClick={() => (isOn ? void go(false) : setAsking(true))}
          >
            {isOn ? words.turnOff : words.turnOn}
          </button>
        </p>
      )}
      {asking && (
        <div class="stack" role="group" aria-label={words.confirmLabel}>
          <p>{words.askOn}</p>
          <p>
            <button type="button" class="button" ref={yes} onClick={() => void go(true)}>
              {words.yesOn}
            </button>{" "}
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setAsking(false);
                setTimeout(() => first.current?.focus(), 0);
              }}
            >
              {words.cancel}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

/** @jsxImportSource preact */
// The Shared story choice on a campaign's page (#2985): Off, Watch the story or Guide me. Turning it on asks for the
// right-to-use confirmation first; the choice is queued for the bot like the other settings, the bot records who
// confirmed and when, and the page follows the answer with the shared wait panel.
import { useEffect, useRef, useState } from "preact/hooks";

import { type StoryMode, storyModePanel as words } from "../content/story-mode-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;
const MODES: StoryMode[] = ["off", "watch", "guide"];

/** The bot's words with Discord's `**bold**` and `code` marks taken out: the page shows plain text. */
const plain = (note: string): string => note.replaceAll("**", "").replaceAll("`", "").replace(/\s*\n+\s*/g, " ").trim();

type Wait = { to: StoryMode; state: CampaignSettingAnswer["state"] | "sending" | "timed_out"; note: string; since: number };

export default function StoryModePanel({ api, campaignRef, mode, onChanged }: { api: AccountApi; campaignRef: string; mode: StoryMode; onChanged?: (mode: StoryMode) => void }) {
  const [current, setCurrent] = useState<StoryMode>(mode);
  const [asking, setAsking] = useState<StoryMode | null>(null);
  const [ticked, setTicked] = useState(false);
  const [wait, setWait] = useState<Wait | null>(null);
  const [problem, setProblem] = useState("");
  const live = useRef(true);
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => () => void (live.current = false), []);
  // The question takes the keyboard focus (the box to tick); the choice buttons are back when it closes.
  useEffect(() => {
    if (asking) box.current?.focus();
  }, [asking]);

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";

  const pick = (to: StoryMode): void => {
    setProblem("");
    if (to === "off") void go(to);
    else {
      setTicked(false);
      setAsking(to);
    }
  };

  const go = async (to: StoryMode): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    setAsking(null);
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ to, state: "sending", note: "", since });
    let queued = false;
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "story_mode", value: to, ...(to === "off" ? {} : { confirm: true }) });
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ to, state: got.state, note: got.note, since });
        if (got.state === "done") {
          setCurrent(to);
          onChanged?.(to);
          return;
        }
        if (got.state === "failed") return;
      }
      // The answer never came: say so and give the buttons back, never a panel that runs for ever.
      if (live.current) setWait({ to, state: "timed_out", note: "", since });
    } catch (err) {
      if (queued) {
        // It was sent; only the look at the answer failed. Do not say it failed: it may well have worked.
        setWait({ to, state: "timed_out", note: "", since });
        return;
      }
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    }
  };

  const direction = wait && wait.to !== "off" ? "on" : "off";
  const failedReason = (w: Wait): string => `${plain(w.note) || words.failed} ${words.stillThat(current)}`;
  return (
    <div class="stack">
      <h3>{words.heading}</h3>
      <p>{words.intro}</p>
      <p>{words.now(current)}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.timedOut}</p>}
      {wait && wait.state !== "timed_out" && (
        <WorkProgress
          stage={wait.state === "done" ? words.done[direction] : wait.state === "working" ? words.working[direction] : words.waiting[direction]}
          startedAt={wait.since}
          state={wait.state === "failed" ? "failed" : wait.state === "done" ? "done" : "running"}
          reason={wait.state === "failed" ? failedReason(wait) : null}
        />
      )}
      {asking === null && (
        <ul class="stack">
          {MODES.filter((m) => m !== current).map((m) => (
            <li key={m}>
              <button type="button" class="button secondary" disabled={busy} onClick={() => pick(m)}>
                {words.choose(m)}
              </button>
              <span class="small muted block">{words.modes[m].help}</span>
            </li>
          ))}
        </ul>
      )}
      {asking !== null && (
        <div class="stack" role="group" aria-label={words.confirmLabel}>
          <p>{words.modes[asking].help}</p>
          <p>{words.warning}</p>
          <label>
            <input type="checkbox" ref={box} checked={ticked} onChange={(e) => setTicked((e.currentTarget as HTMLInputElement).checked)} /> {words.rightLabel}
          </label>
          <p>
            <button type="button" class="button" disabled={!ticked} onClick={() => void go(asking)}>
              {words.confirm}
            </button>{" "}
            <button type="button" class="button secondary" onClick={() => setAsking(null)}>
              {words.keep}
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

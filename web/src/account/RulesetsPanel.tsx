/** @jsxImportSource preact */
// Main rules and "If missing" on a campaign's page, for its game masters (#2985): the two choices Discord's picker has.
// Sent as one queued choice, "<main>/<fallback>"; the page follows the answer with the shared wait panel. The fallback
// can never be the main rules themselves (the page won't offer it, and the server and the bot refuse it too).
import { useEffect, useRef, useState } from "preact/hooks";

import { type Fallback, FALLBACK_NONE, MAIN_CHOICES, type Main, rulesetsPanel as words } from "../content/rulesets-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

/** The bot's words with Discord's `**bold**` and `code` marks taken out: the page shows plain text. */
const plain = (note: string): string => note.replaceAll("**", "").replaceAll("`", "").replace(/\s*\n+\s*/g, " ").trim();

type Wait = { state: CampaignSettingAnswer["state"] | "sending" | "timed_out"; note: string; since: number };

const fallbackChoices = (main: Main): Fallback[] => [...MAIN_CHOICES.filter((m) => m !== main), FALLBACK_NONE];

export default function RulesetsPanel({ api, campaignRef, main, fallback, onChanged }: { api: AccountApi; campaignRef: string; main: Main; fallback: Fallback; onChanged?: (main: Main, fallback: Fallback) => void }) {
  const [current, setCurrent] = useState<{ main: Main; fallback: Fallback }>({ main, fallback });
  const [pickMain, setPickMain] = useState<Main>(main);
  const [pickFallback, setPickFallback] = useState<Fallback>(fallback);
  const [problem, setProblem] = useState("");
  const [wait, setWait] = useState<Wait | null>(null);
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";
  const unchanged = pickMain === current.main && pickFallback === current.fallback;

  function chooseMain(next: Main) {
    setPickMain(next);
    // The fallback may not be the main rules: if it was, "skip it" is the safe answer.
    if (pickFallback === next) setPickFallback(FALLBACK_NONE);
  }

  const go = async (): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer || unchanged) return;
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ state: "sending", note: "", since });
    let queued = false;
    const asked = { main: pickMain, fallback: pickFallback };
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "rulesets", value: `${asked.main}/${asked.fallback}` });
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ state: got.state, note: got.note, since });
        if (got.state === "done") {
          setCurrent(asked);
          onChanged?.(asked.main, asked.fallback);
          return;
        }
        if (got.state === "failed") {
          setPickMain(current.main); // the choice did not happen: show what is true
          setPickFallback(current.fallback);
          return;
        }
      }
      if (live.current) setWait({ state: "timed_out", note: "", since });
    } catch (err) {
      if (queued) {
        // It was sent; only the look at the answer failed. Do not say it failed: it may well have worked.
        setWait({ state: "timed_out", note: "", since });
        return;
      }
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    }
  };

  return (
    <section class="stack" aria-labelledby="rulesets-heading">
      <h3 id="rulesets-heading">{words.heading}</h3>
      <p class="small">{words.intro}</p>
      <p>{words.now(current.main, current.fallback)}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.timedOut}</p>}
      {wait && wait.state === "done" && <p role="status">{words.done}</p>}
      {wait && wait.state !== "timed_out" && (
        <WorkProgress
          stage={wait.state === "done" ? words.done : wait.state === "working" ? words.working : words.waiting}
          startedAt={wait.since}
          state={wait.state === "failed" ? "failed" : wait.state === "done" ? "done" : "running"}
          reason={wait.state === "failed" ? `${words.failed} ${plain(wait.note) || words.failedNext} ${words.now(current.main, current.fallback)}`.replace(/\s+/g, " ").trim() : null}
        />
      )}
      <form class="stack" onSubmit={(e) => { e.preventDefault(); void go(); }}>
        <fieldset class="stack" disabled={busy}>
          <legend>{words.mainLegend}</legend>
          {MAIN_CHOICES.map((m) => (
            <label key={m}>
              <input type="radio" name="rulesets-main" value={m} checked={pickMain === m} onChange={() => chooseMain(m)} /> {words.main[m]}
            </label>
          ))}
        </fieldset>
        <fieldset class="stack" disabled={busy}>
          <legend>{words.fallbackLegend}</legend>
          {fallbackChoices(pickMain).map((f) => (
            <label key={f}>
              <input type="radio" name="rulesets-fallback" value={f} checked={pickFallback === f} onChange={() => setPickFallback(f)} /> {words.fallback[f]}
            </label>
          ))}
        </fieldset>
        <p>
          <button type="submit" class="button" disabled={busy || unchanged}>{words.save}</button>
        </p>
      </form>
    </section>
  );
}

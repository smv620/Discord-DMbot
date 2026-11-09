/** @jsxImportSource preact */
// "Make the script" on a session's Script tab (#3899, owner request 2026-11-09): a game master presses once, with the cost shown
// first (the line Discord's question gives); the bot makes it with the same job as Discord's button, and the page shows the
// shared wait panel (stage, count, how long, last moved) until it is done, then tells the tab to read the script. New sessions
// get their script by themselves (#3901); this is for the sessions that have none, by hand, because it uses the AI allowance.
// Game masters only (the tab is theirs; the door refuses anyone else). If the page is opened while the script is already being
// made (a reload, or another tab pressed it), it follows that job without a button, so a second press can never queue a second charge.
import { useEffect, useRef, useState } from "preact/hooks";

import { makeScriptWords as w } from "../content/make-script-words";
import { ApiError, type AccountApi, type JobProgress, type ScriptOffer } from "./api";
import WorkProgress from "./WorkProgress";

const FOLLOW_MS = 3000;

export default function MakeScript({ api, campaignRef, number, onMade }: { api: AccountApi; campaignRef: string; number: number; onMade: () => void }) {
  const [offer, setOffer] = useState<ScriptOffer | "loading" | "none" | "failed">("loading");
  const [making, setMaking] = useState(false);
  const [look, setLook] = useState<JobProgress | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  const flight = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  // The offer is read once, and again every few seconds while the script is already being made, until it exists.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const read = (): void => {
      api.scriptOffer?.(campaignRef, number).then(
        (got) => {
          if (!alive) return;
          setOffer(got);
          if (got.state === "running") timer = setTimeout(read, FOLLOW_MS);
          else if (got.state === "exists" && flight.current === false && making === false && offerWasRunning.current) onMade();
          offerWasRunning.current = got.state === "running";
        },
        (error: unknown) => {
          if (!alive) return;
          // A player gets nothing here (the door refuses); a game master with a trouble reading is told, with the page left as it is.
          setOffer(error instanceof ApiError && error.kind === "not-allowed" ? "none" : "failed");
        },
      );
    };
    const offerWasRunning = { current: false };
    read();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef, number]);

  async function make(): Promise<void> {
    if (flight.current || !api.makeScript) return;
    flight.current = true;
    setMaking(true);
    setProblem(null);
    setLook(null);
    setStartedAt(Math.floor(Date.now() / 1000));
    try {
      await api.makeScript(campaignRef, number, (p) => live.current && setLook(p));
      if (live.current) onMade();
    } catch (error) {
      if (!live.current) return;
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(w.problems[kind] ?? w.failed);
      if (kind === "script-already") onMade();
    } finally {
      flight.current = false;
      if (live.current) setMaking(false);
    }
  }

  if (offer === "loading" || offer === "none") return null;
  if (offer === "failed") return <p class="muted" role="status">{w.failed}</p>;
  if (offer.state === "exists") return <p role="status">{w.already}</p>;
  if (!offer.allowed) return <p class="muted" role="status">{offer.why === "plan" ? w.plan : w.off}</p>;
  const running = offer.state === "running";
  const p = making ? look : (offer.progress ?? null);
  return (
    <div class="stack">
      <p>{w.lead}</p>
      {offer.cost && !running && <p class="small">{offer.cost}</p>}
      {(making || running) && (
        <WorkProgress
          stage={w.stage}
          done={p && typeof p.done === "number" && (p.total ?? 0) > 0 ? p.done : null}
          total={p && (p.total ?? 0) > 0 ? p.total : null}
          unit={p?.unit ?? undefined}
          startedAt={p?.startedAt ?? (startedAt || null)}
          updatedAt={p?.updatedAt ?? null}
          serverNow={p?.serverNow ?? null}
          state={p?.state === "stalled" ? "stalled" : "running"}
          progressUnknown={!p}
          whatToDo={w.quiet}
        />
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {!running && (
        <p>
          <button type="button" class="button" disabled={making} aria-busy={making} onClick={() => void make()}>
            {making ? w.making : w.button}
          </button>
        </p>
      )}
    </div>
  );
}

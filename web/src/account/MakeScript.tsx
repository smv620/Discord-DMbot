/** @jsxImportSource preact */
// "Make the script" on a session's Script tab (#3899, owner request 2026-11-09): a game master presses once, with the cost shown
// first (the line Discord's question gives); the bot makes it with the same job as Discord's button, and the page shows the
// shared wait panel (stage, count, how long, last moved) until it is done, then tells the tab to read the script. New sessions
// get their script by themselves (#3901); this is for the sessions that have none, by hand, because it uses the AI allowance.
// Game masters only (the tab is theirs; the door refuses anyone else).
import { useEffect, useRef, useState } from "preact/hooks";

import { makeScriptWords as w } from "../content/make-script-words";
import { ApiError, type AccountApi, type JobProgress, type ScriptOffer } from "./api";
import WorkProgress from "./WorkProgress";

export default function MakeScript({ api, campaignRef, number, onMade }: { api: AccountApi; campaignRef: string; number: number; onMade: () => void }) {
  const [offer, setOffer] = useState<ScriptOffer | "loading" | "none">("loading");
  const [making, setMaking] = useState(false);
  const [look, setLook] = useState<JobProgress | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  const flight = useRef(false);
  useEffect(() => () => void (live.current = false), []);
  useEffect(() => {
    let alive = true;
    setOffer("loading");
    api.scriptOffer?.(campaignRef, number).then(
      (got) => alive && setOffer(got),
      () => alive && setOffer("none"), // (a failed read offers nothing: the tab still says there is no script)
    );
    return () => {
      alive = false;
    };
  }, [api, campaignRef, number]);

  async function make(): Promise<void> {
    if (flight.current || !api.makeScript) return;
    flight.current = true;
    setMaking(true);
    setProblem(null);
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
  if (offer.state === "exists") return <p role="status">{w.already}</p>;
  if (!offer.allowed) return <p class="muted" role="status">{offer.why === "plan" ? w.plan : w.off}</p>;
  return (
    <div class="stack">
      <p>{w.lead}</p>
      {offer.cost && <p class="small">{offer.cost}</p>}
      {(making || offer.state === "running") && (
        <WorkProgress
          stage={w.stage}
          done={look && typeof look.done === "number" && (look.total ?? 0) > 0 ? look.done : null}
          total={look && (look.total ?? 0) > 0 ? look.total : null}
          unit={look?.unit ?? undefined}
          startedAt={look?.startedAt ?? (startedAt || null)}
          updatedAt={look?.updatedAt ?? null}
          serverNow={look?.serverNow ?? null}
          state={look?.state === "stalled" ? "stalled" : "running"}
          progressUnknown={!look}
          whatToDo={w.quiet}
        />
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      <p>
        <button type="button" class="button" disabled={making} aria-busy={making} onClick={() => void make()}>
          {making ? w.making : w.button}
        </button>
      </p>
    </div>
  );
}

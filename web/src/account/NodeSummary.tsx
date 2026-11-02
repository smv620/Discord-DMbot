// The short summary of a picked name, in the Story Graph's side panel (#3243). One request per pick; a new pick
// ignores the answer to the old one. Players see a spoiler gate first, and nothing of the hidden text is rendered.
import { useEffect, useRef, useState } from "preact/hooks";

import { nodeSummaryWords as w } from "../content/node-summary-words";
import { NODE_SUMMARY_LIVE } from "../content/node-summary-switch";
import { ApiError, type AccountApi, type NodeSummary as Summary } from "./api";

type State =
  | { kind: "loading" }
  | { kind: "gate" }
  | { kind: "text"; summary: Summary }
  | { kind: "notFound" }
  | { kind: "error" };

export default function NodeSummary({ api, refId, id }: { api: AccountApi; refId: string; id: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [seconds, setSeconds] = useState(0);
  const [cooling, setCooling] = useState(false);
  // Each request gets a number; an answer that arrives after a newer pick, or after a retry, is dropped.
  const latest = useRef(0);
  const cooldown = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (cooldown.current) clearTimeout(cooldown.current);
  }, []);

  const ask = (peek: boolean): void => {
    if (!api.webNodeSummary) return;
    const mine = ++latest.current;
    setState({ kind: "loading" });
    setSeconds(0);
    api.webNodeSummary(refId, id, peek).then(
      (summary) => {
        if (mine !== latest.current) return;
        if (summary.needsPeek && !peek) setState({ kind: "gate" });
        // An answer with no text is not something to show: it counts as a failure, not an empty paragraph.
        else if (!summary.text) setState({ kind: "error" });
        else setState({ kind: "text", summary });
      },
      (err: unknown) => {
        if (mine !== latest.current) return;
        setState({ kind: err instanceof ApiError && err.kind === "not-found" ? "notFound" : "error" });
      },
    );
  };

  // A new pick (or a new campaign) starts again from the first request.
  useEffect(() => {
    if (!NODE_SUMMARY_LIVE || !api.webNodeSummary) return;
    ask(false);
    return () => {
      latest.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId, id]);

  // While it reads, count the seconds, so a slow answer shows that DMbot is still working.
  const loading = state.kind === "loading";
  useEffect(() => {
    if (!loading) return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [loading]);

  if (!NODE_SUMMARY_LIVE || !api.webNodeSummary) return null;

  const retry = (): void => {
    setCooling(true);
    if (cooldown.current) clearTimeout(cooldown.current);
    cooldown.current = setTimeout(() => setCooling(false), 1000);
    ask(false);
  };

  return (
    // One live region for the answer as a whole; the counting words are plain text, so a screen reader does not
    // read the count out every second.
    <section class="stack small" aria-live="polite">
      {state.kind === "loading" && (
        <p class="small muted">
          {seconds >= 30 ? w.stalled(seconds) : seconds >= 5 ? w.slow(seconds) : w.loading}
        </p>
      )}
      {state.kind === "gate" && (
        <>
          <p class="small">{w.spoilers}</p>
          <button type="button" class="button secondary" onClick={() => ask(true)}>
            {w.showAnyway}
          </button>
        </>
      )}
      {state.kind === "text" && (
        <>
          <p>{state.summary.text}</p>
          {state.summary.version === "dm" && <p class="small muted">{w.dmHint}</p>}
        </>
      )}
      {state.kind === "notFound" && <p class="small">{w.notFound}</p>}
      {state.kind === "error" && (
        <>
          <p class="small">{w.error}</p>
          <button type="button" class="button secondary" disabled={cooling} onClick={retry}>
            {w.tryAgain}
          </button>
        </>
      )}
    </section>
  );
}

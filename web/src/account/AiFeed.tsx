/** @jsxImportSource preact */
// The live, read-only windows on what DMbot asked the AI and what came back, under the wait panel of a lore upload or an
// import (#3710). Two code-style windows (what was asked, what came back), newest at the bottom; each entry is the first
// 500 characters then "[...]" (cut before it was stored). Read-only plain text: no buttons on an entry, and never shown
// to anyone but a campaign's DMs and game masters (the server decides). While the job runs it looks again every few
// seconds, sending only the time it has reached; the calls still waiting always come back, so a reply arrives where its
// prompt already is. It stays at the newest line unless the reader scrolled up, then a button jumps there.
import { useEffect, useRef, useState } from "preact/hooks";

import { feedWords as w } from "../content/ai-feed-words";
import type { AccountApi, AiFeedEntry } from "./api";

const EVERY_MS = 3000;
const TRIES = 3; // looks that fail in a row before the window says it stopped
const NEAR_BOTTOM = 12; // pixels from the end that still count as "at the newest"

const keyOf = (e: AiFeedEntry): string => `${e.job}:${e.n}`;
const clock = (at: number): string => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const head = (e: AiFeedEntry): string => `#${e.n} ${clock(e.at)} ${[e.service, e.model].filter(Boolean).join(", ")}${e.step ? `, ${e.step}` : ""}`;

function textOf(entries: AiFeedEntry[], which: "prompt" | "reply"): string {
  return entries
    .map((e) => {
      const body =
        which === "prompt"
          ? e.prompt
          : e.state === "failed"
            ? w.failedReply(e.reason ?? "")
            : e.reply === null
              ? w.waitingReply
              : e.reply;
      return `${head(e)}\n${body}`;
    })
    .join("\n\n");
}

export default function AiFeed({
  api,
  campaignRef,
  kind,
  job,
  running,
  everyMs = EVERY_MS,
}: {
  api: AccountApi;
  campaignRef: string;
  kind: "lore" | "import";
  /** The lore upload's id, or the import's. */
  job: string;
  /** True while the job is going: it is looked at until a final look after it ends. */
  running: boolean;
  everyMs?: number;
}) {
  const [entries, setEntries] = useState<Map<string, AiFeedEntry>>(new Map());
  const [lost, setLost] = useState(0);
  const [stopped, setStopped] = useState(false);
  const [behind, setBehind] = useState<{ prompt: boolean; reply: boolean }>({ prompt: false, reply: false });
  const after = useRef(0);
  const runningNow = useRef(running);
  runningNow.current = running;
  const stick = useRef({ prompt: true, reply: true });
  const boxes = { prompt: useRef<HTMLPreElement>(null), reply: useRef<HTMLPreElement>(null) };

  useEffect(() => {
    if (!api.aiFeed) return;
    let live = true;
    let fails = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async (last: boolean): Promise<void> => {
      try {
        const got = await api.aiFeed!(campaignRef, kind, job, after.current);
        if (!live) return;
        fails = 0;
        setLost(got.lost);
        if (got.entries.length > 0) {
          after.current = Math.max(after.current, ...got.entries.map((e) => e.at));
          setEntries((was) => {
            const next = new Map(was);
            for (const e of got.entries) next.set(keyOf(e), e);
            return next;
          });
        }
      } catch {
        if (!live) return;
        fails += 1;
        if (fails >= TRIES) {
          setStopped(true);
          return;
        }
      }
      // One more look after the job ends, to catch the last reply; then it rests.
      if (live && (runningNow.current || !last)) timer = setTimeout(() => void look(!runningNow.current), everyMs);
    };
    void look(false);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, campaignRef, kind, job]);

  const list = [...entries.values()].sort((a, b) => a.at - b.at || a.job.localeCompare(b.job) || a.n - b.n);
  const prompts = textOf(list, "prompt");
  const replies = textOf(list, "reply");

  // New text: stay at the newest line, unless the reader scrolled up (then say there is more below).
  useEffect(() => {
    for (const which of ["prompt", "reply"] as const) {
      const box = boxes[which].current;
      if (!box) continue;
      if (stick.current[which]) box.scrollTop = box.scrollHeight;
      else setBehind((b) => (b[which] ? b : { ...b, [which]: true }));
    }
  }, [prompts, replies]);

  if (!api.aiFeed) return null;
  const onScroll = (which: "prompt" | "reply") => () => {
    const box = boxes[which].current;
    if (!box) return;
    const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight <= NEAR_BOTTOM;
    stick.current[which] = atEnd;
    if (atEnd) setBehind((b) => (b[which] ? { ...b, [which]: false } : b));
  };
  const jump = (which: "prompt" | "reply") => () => {
    const box = boxes[which].current;
    if (box) box.scrollTop = box.scrollHeight;
    stick.current[which] = true;
    setBehind((b) => ({ ...b, [which]: false }));
  };
  const windowOf = (which: "prompt" | "reply", heading: string, text: string) => (
    <div class="stack">
      <h4 id={`ai-feed-${which}`}>{heading}</h4>
      <pre
        class="code-window"
        ref={boxes[which]}
        tabIndex={0}
        role="region"
        aria-label={w.windowLabel(heading)}
        onScroll={onScroll(which)}
      >
        {text === "" ? w.none : text}
      </pre>
      {behind[which] && (
        <button type="button" class="button secondary" onClick={jump(which)}>
          {w.jump}
        </button>
      )}
    </div>
  );
  return (
    <section class="stack" aria-labelledby="ai-feed-h" id="ai-feed">
      <h3 id="ai-feed-h">{w.heading}</h3>
      <p class="small muted">{w.lead}</p>
      {lost > 0 && <p class="small muted">{w.lost(lost)}</p>}
      {windowOf("prompt", w.promptsHeading, prompts)}
      {windowOf("reply", w.repliesHeading, replies)}
      {stopped && (
        <p class="warn" role="status">
          {w.stopped}
        </p>
      )}
    </section>
  );
}

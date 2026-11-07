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
const SLOW_MS = 15000; // after failed looks it keeps trying, slower: a blip must not end the feed for good
const TRIES = 3; // looks that fail in a row before the window says it is out of date
const PAGE = 200; // the most calls the server sends at once
const NEAR_BOTTOM = 12; // pixels from the end that still count as "at the newest"

const keyOf = (e: AiFeedEntry): string => `${e.job}:${e.n}`;
const clock = (at: number): string => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const head = (e: AiFeedEntry, label: string): string => `${label} ${e.n} · ${clock(e.at)}${e.step ? ` · ${e.step}` : ""}`;

function textOf(entries: AiFeedEntry[], which: "prompt" | "reply", running: boolean): string {
  return entries
    .map((e) => {
      const body =
        which === "prompt"
          ? e.prompt
          : e.state === "failed"
            ? w.failedReply
            : e.reply === null
              ? running
                ? w.waitingReply
                : w.noReply
              : e.reply;
      return `${head(e, which === "prompt" ? w.question : w.answer)}\n${body}`;
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
  const [stalled, setStalled] = useState(false);
  const [behind, setBehind] = useState<{ prompt: boolean; reply: boolean }>({ prompt: false, reply: false });
  const after = useRef(0);
  const seen = useRef<Map<string, AiFeedEntry>>(new Map());
  const runningNow = useRef(running);
  runningNow.current = running;
  const stick = useRef({ prompt: true, reply: true });
  const boxes = { prompt: useRef<HTMLPreElement>(null), reply: useRef<HTMLPreElement>(null) };

  useEffect(() => {
    if (!api.aiFeed) return;
    let live = true;
    let fails = 0;
    // Another job (or campaign): nothing of the last one stays, and the look starts from the beginning.
    after.current = 0;
    seen.current = new Map();
    setEntries(new Map());
    setLost(0);
    setStalled(false);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let busy = false; // a look is on its way: a second one must not start beside it
    let finalLook = false; // the look after the job ended has been made, so the window rests
    const look = async (last: boolean): Promise<void> => {
      // A hidden tab asks nothing (#3710): the visibility handler below looks again when it is shown.
      if (document.hidden) return;
      busy = true;
      if (last) finalLook = true;
      try {
        const got = await api.aiFeed!(campaignRef, kind, job, after.current);
        if (!live) return;
        fails = 0;
        setStalled(false);
        setLost(got.lost);
        if (got.entries.length > 0) {
          const next = new Map(seen.current);
          for (const e of got.entries) next.set(keyOf(e), e);
          seen.current = next;
          setEntries(next);
          // Ask again from the oldest call still waiting (it may have finished, and a finished call keeps its old
          // time), else from the newest. The same second is sent again; calls are told apart by job and number.
          const open = [...next.values()].filter((e) => e.state === "waiting").map((e) => e.at);
          after.current = open.length > 0 ? Math.min(...open) : Math.max(after.current, ...got.entries.map((e) => e.at));
        }
        // A full page means more is waiting to be read: catch up at once, not in a few seconds.
        if (got.entries.length >= PAGE && live && (runningNow.current || !last)) {
          timer = setTimeout(() => void look(!runningNow.current), 0);
          return;
        }
      } catch {
        if (!live) return;
        fails += 1;
        if (fails >= TRIES) setStalled(true);
      } finally {
        busy = false;
      }
      // One more look after the job ends, to catch the last reply; then it rests. After failed looks it asks
      // less often but keeps going, and clears the note on the first look that works. A hidden tab sets no timer.
      if (live && !document.hidden && (runningNow.current || !last))
        timer = setTimeout(() => void look(!runningNow.current), fails >= TRIES ? SLOW_MS : everyMs);
    };
    // Shown again (#3710): one look at once if the job is still going, or its final look has not been made yet, then
    // the usual rhythm. A look already on its way sets the next one itself.
    const shown = (): void => {
      if (document.hidden || !live || busy) return;
      if (timer !== undefined) clearTimeout(timer);
      if (runningNow.current || !finalLook) void look(!runningNow.current);
    };
    document.addEventListener("visibilitychange", shown);
    void look(false);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", shown);
    };
  }, [api, campaignRef, kind, job]);

  const list = [...entries.values()].sort((a, b) => a.at - b.at || a.job.localeCompare(b.job) || a.n - b.n);
  const prompts = textOf(list, "prompt", running);
  const replies = textOf(list, "reply", running);

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
        id={`ai-feed-box-${which}`}
        ref={boxes[which]}
        tabIndex={0}
        role="region"
        aria-label={w.windowLabel(heading)}
        onScroll={onScroll(which)}
      >
        {text === "" ? (which === "prompt" ? w.nonePrompts : w.noneReplies) : text}
      </pre>
      {behind[which] && (
        <button type="button" class="button secondary" aria-controls={`ai-feed-box-${which}`} onClick={jump(which)}>
          {w.jump}
        </button>
      )}
    </div>
  );
  return (
    <section class="stack" aria-labelledby="ai-feed-h" id="ai-feed">
      <h3 id="ai-feed-h">{w.heading}</h3>
      <p class="small muted">{w.lead}</p>
      {/* Always there, filled only when needed, so a screen reader hears it appear. */}
      <p class="warn" role="status">
        {stalled ? w.stalled : ""}
      </p>
      {lost > 0 && <p class="small muted">{w.lost(lost)}</p>}
      {windowOf("prompt", w.promptsHeading, prompts)}
      {windowOf("reply", w.repliesHeading, replies)}
    </section>
  );
}

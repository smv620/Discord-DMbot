/** @jsxImportSource preact */
// What the recap page shows while a recap goes out or its audio is made (#2419, owner 2026-10-22):
// "Getting it ready for Discord…" (or "Making the audio…"), the step the bot is on, and a running
// clock counted from when the work started on the server, so leaving and coming back shows the same
// time. The page's own polling (Recaps.tsx) brings the new answers; this only shows them. The step
// is read out when it changes (role="status"); the clock is not in a live region, so it is not
// announced every second.
import { useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/recaps";
import type { Recap } from "./api";

/** m:ss, whole seconds, never negative. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

/** The message's link, only if it is a Discord address (it comes from the bot's own store, but a
 * link on the page is never trusted to be anything else). */
export function discordLink(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const found = new URL(url);
    const host = found.hostname.toLowerCase();
    const ok = host === "discord.com" || host.endsWith(".discord.com") || host === "discordapp.com";
    return found.protocol === "https:" && ok ? found.toString() : null;
  } catch {
    return null;
  }
}

/** After this long with no end, say that leaving the page is fine. */
const SLOW_SECONDS = 90;

export default function RecapProgress({ recap }: { recap: Recap }) {
  const going = recap.status === "posting";
  const audio = !going && recap.audio?.state === "queued";
  // The server's clock now, kept as the gap from this browser's clock when the answer arrived.
  const gap = useRef(0);
  const lastServerNow = useRef<number | undefined>(undefined);
  // When this page first saw the work going on, for a bot that doesn't say when it began: set when
  // it starts, not when the page loaded, so a DM who sat on the page sees 0:00 after pressing Post.
  const activeSince = useRef<number | null>(null);
  const [, redraw] = useState(0);
  // Taken once per answer (not on every redraw of the clock, which would freeze it).
  if (typeof recap.serverNow === "number" && recap.serverNow !== lastServerNow.current) {
    lastServerNow.current = recap.serverNow;
    gap.current = recap.serverNow - Date.now() / 1000;
  }
  if (going || audio) {
    if (activeSince.current === null) activeSince.current = Date.now() / 1000;
  } else activeSince.current = null;
  useEffect(() => {
    if (!going && !audio) return;
    const timer = setInterval(() => redraw((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [going, audio]);
  if (!going && !audio) return null;

  // From the server's own start when it says so; otherwise from when this page first saw it.
  const shared = recap.postingProgress;
  const start =
    typeof shared?.startedAt === "number" && shared.startedAt > 0
      ? shared.startedAt
      : typeof recap.postingSince === "number" && recap.postingSince > 0
        ? recap.postingSince
        : (activeSince.current ?? Date.now() / 1000);
  const now = Date.now() / 1000 + (typeof recap.serverNow === "number" ? gap.current : 0);
  // The step from the shared stage when the old field is missing (the API sends both from #3497).
  const sharedStep = ({ posting_check: "check", posting_post: "post", posting_audio: "audio" } as const)[shared?.stage as "posting_check"];
  const said = recap.postingStep ?? sharedStep ?? null;
  const step = going ? (said === "audio" ? null : said) : "audio";
  const stalled = shared?.state === "stalled";
  return (
    <section class="panel" aria-labelledby="recap-progress">
      <h3 id="recap-progress">{going ? text.posting : text.audioMaking}</h3>
      <p class="small" role="status" aria-live="polite">
        {step ? text.step[step] : ""}
      </p>
      {stalled ? (
        <p class="small" role="status">
          {text.postStalled}
        </p>
      ) : (
        now - start >= SLOW_SECONDS && <p class="small muted">{text.slow}</p>
      )}
      <p class="small muted">
        {text.clockLabel}: <span role="timer" aria-live="off">{clock(now - start)}</span>
      </p>
    </section>
  );
}

/** @jsxImportSource preact */
// "Speak instead" on a recap's page (#2114; core #2113 part 2): the DM records a short note, the API
// turns it into words and deletes the recording, and the words are put in the "Tell DMbot what to
// fix" box to check before Send. Nothing is sent until the DM presses Stop, the recording is never
// kept on this page, and the browser asks for the microphone only when the DM presses the button.
// A browser that can't record in webm or ogg (Safari today) sees "type your note instead".
import { useEffect, useRef, useState } from "preact/hooks";

import {
  VOICE_MAX_BYTES,
  VOICE_STOP_AT_SECONDS,
  voiceWords as words,
} from "../content/recap-voice";
import { ApiError, type AccountApi, type Recap } from "./api";

const POLL_MS = 2000;
const POLL_LIMIT = 60; // about two minutes: turning a note into words can take a while

/** The first recording format this browser makes that the API reads (webm or ogg). */
const pickType = (): string | null => {
  if (typeof MediaRecorder === "undefined") return null;
  for (const type of [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ])
    if (MediaRecorder.isTypeSupported(type)) return type;
  return null;
};

export default function RecapVoice({
  api,
  recap,
  onHeard,
}: {
  api: AccountApi;
  recap: Recap;
  onHeard: (text: string) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "asking" | "recording" | "sending">("idle");
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [checks, setChecks] = useState(0);
  const [heard, setHeard] = useState(false);
  const [atLimit, setAtLimit] = useState(false);
  const stopRef = useRef<HTMLButtonElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const mounted = useRef(true);

  const release = (): void => {
    if (ticker.current) clearInterval(ticker.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  };

  // Leaving the page ends the recording and lets go of the microphone; nothing is sent.
  useEffect(
    () => () => {
      mounted.current = false;
      const rec = recorder.current;
      if (rec) {
        rec.onstop = null;
        if (rec.state !== "inactive") rec.stop();
      }
      release();
    },
    [],
  );

  const fail = (error: unknown): void => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(words.problem[kind] ?? words.problem.server);
  };

  // Follow the request until the words are ready.
  useEffect(() => {
    const read = api.voiceResult;
    if (!requestId || !read) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    let retried = false;
    const tick = async (): Promise<void> => {
      try {
        const got = await read(recap.ref, requestId);
        if (!live) return;
        retried = false; // a later blip gets its own second look
        if (got.state === "done") {
          if (typeof got.text !== "string" || got.text === "") throw new ApiError("server");
          setPhase("idle");
          setRequestId(null);
          setHeard(true);
          onHeard(got.text);
          return;
        }
        if (got.state === "failed") {
          setPhase("idle");
          setRequestId(null);
          const code = (got.error ?? "").replace("no_consent", "no-consent").replace("bad_clip", "bad-clip");
          setProblem(
            Object.hasOwn(words.problem, code) ? words.problem[code]! : words.problem.server,
          );
          return;
        }
        polls += 1;
        if (polls >= POLL_LIMIT) {
          setPhase("idle");
          setSlow(true);
          return;
        }
        timer = setTimeout(() => void tick(), POLL_MS);
      } catch (error) {
        if (!live) return;
        if (error instanceof ApiError && error.kind === "network" && !retried) {
          retried = true;
          timer = setTimeout(() => void tick(), POLL_MS);
          return;
        }
        setPhase("idle");
        setRequestId(null);
        fail(error);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // onHeard is the parent's setter; it must not restart the polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, recap.ref, requestId, checks]);

  // The button that was pressed is replaced by Stop, so focus follows it.
  useEffect(() => {
    if (phase === "recording") stopRef.current?.focus();
  }, [phase]);

  const type = pickType();
  if (!api.sendVoice || !api.voiceResult || !recap.canEdit) return null;
  if (!type || !navigator.mediaDevices?.getUserMedia)
    return <p class="muted small">{words.unsupported}</p>;

  const send = async (clip: Blob): Promise<void> => {
    try {
      const { requestId: id } = await api.sendVoice!(recap.ref, clip);
      if (!mounted.current) return;
      setPhase("sending");
      setRequestId(id);
    } catch (error) {
      if (!mounted.current) return;
      setPhase("idle");
      fail(error);
    }
  };

  const start = async (): Promise<void> => {
    setProblem(null);
    setSlow(false);
    setHeard(false);
    setAtLimit(false);
    setSeconds(0);
    setPhase("asking");
    let got: MediaStream;
    try {
      got = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (!mounted.current) return;
      setPhase("idle");
      setProblem(
        (error as { name?: string }).name === "NotFoundError"
          ? words.problem["no-mic"] ?? words.problem.server
          : words.problem.denied ?? words.problem.server,
      );
      return;
    }
    if (!mounted.current) {
      got.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = got;
    const chunks: Blob[] = [];
    let rec: MediaRecorder;
    try {
      rec = new MediaRecorder(got, { mimeType: type });
    } catch {
      release();
      setPhase("idle");
      setProblem(words.problem["bad-type"] ?? words.problem.server);
      return;
    }
    recorder.current = rec;
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = () => {
      release();
      // Sent as plain webm or ogg: the API names the types it reads, without the codec part.
      const clip = new Blob(chunks, { type: type.split(";")[0] as string });
      if (clip.size === 0) {
        setPhase("idle");
        setProblem(words.empty);
        return;
      }
      if (clip.size > VOICE_MAX_BYTES) {
        setPhase("idle");
        setProblem(words.problem["too-big"] ?? words.problem.server);
        return;
      }
      setPhase("sending");
      void send(clip);
    };
    try {
      rec.start();
    } catch {
      release();
      setPhase("idle");
      setProblem(words.problem["bad-type"] ?? words.problem.server);
      return;
    }
    setPhase("recording");
    let elapsed = 0;
    ticker.current = setInterval(() => {
      elapsed += 1;
      setSeconds(elapsed);
      if (elapsed >= VOICE_STOP_AT_SECONDS) {
        setAtLimit(true);
        stop();
      }
    }, 1000);
  };

  const stop = (): void => {
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
  };

  // The timer is not in the live region: a screen reader would read every second.
  const status =
    phase === "asking"
      ? words.starting
      : phase === "recording"
        ? words.recordingNow
        : phase === "sending"
          ? atLimit
            ? `${words.atLimit} ${words.sending}`
            : words.sending
          : slow
            ? words.slow
            : heard
              ? words.heard
              : "";

  return (
    <div class="stack">
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      <p role="status" aria-live="polite" class="small">
        {status}
      </p>
      {phase === "recording" ? (
        <>
          <button
            type="button"
            class="button"
            ref={stopRef}
            aria-label={words.stopLabel}
            onClick={stop}
          >
            {words.stop}
          </button>
          <span class="small" aria-hidden="true">
            {words.timer(seconds)}
          </span>
        </>
      ) : (
        <button
          type="button"
          class="button secondary"
          disabled={phase !== "idle"}
          onClick={() => void start()}
        >
          {words.speak}
        </button>
      )}
      {slow && requestId && (
        <button
          type="button"
          class="button secondary"
          onClick={() => {
            setSlow(false);
            setPhase("sending");
            setChecks((n) => n + 1);
          }}
        >
          {words.checkAgain}
        </button>
      )}
      <p class="muted small">{words.privacy}</p>
    </div>
  );
}

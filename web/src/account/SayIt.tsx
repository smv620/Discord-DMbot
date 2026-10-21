/** @jsxImportSource preact */
// "How to say it" (#2317; docs/PLAN.md): the buttons that go with a name's "said as" box. 💡 Suggest
// fills the box with a respelling of the written name. 🎤 Say it records the DM in the browser (the
// microphone is asked for only on the press, at most CLIP_SECONDS seconds), lets them play it back
// before anything is sent, and on Use this sends the one clip; the respelling that comes back goes
// in the box. 🔊 Hear it reads the box aloud with the browser's own voice. Everything lands in the
// box UNSAVED: the DM changes it and saves. The clip is never kept here (the API deletes it too).
import { useEffect, useRef, useState } from "preact/hooks";

import { CLIP_SECONDS, sayIt as words } from "../content/say-it-words";
import { ApiError, type AccountApi } from "./api";
import { pickType } from "./recording";

type Phase = "idle" | "asking" | "recording" | "recorded" | "sending";

// The microphone is one thing: a second name starting to record would take it from the first.
let recordingFor: string | null = null;

export default function SayIt({
  api,
  campaignRef,
  nameId,
  name,
  value,
  onValue,
  disabled,
}: {
  api: AccountApi;
  campaignRef: string;
  nameId: string;
  /** The name as written, for the recording's label. */
  name: string;
  /** What is in the box now (for Hear it). */
  value: string;
  /** Puts words in the box, unsaved. */
  onValue: (text: string, how: "suggested" | "heard") => void;
  disabled?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [clipUrl, setClipUrl] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const clip = useRef<Blob | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const throwAway = useRef(false);
  const mounted = useRef(true);
  const stopButton = useRef<HTMLButtonElement>(null);
  const recordButton = useRef<HTMLButtonElement>(null);

  const canSuggest = Boolean(api.suggestSaidAs);
  const type = typeof window === "undefined" ? null : pickType();
  const canRecord = Boolean(api.respellFromVoice) && type !== null && Boolean(navigator.mediaDevices?.getUserMedia);
  const canHear = typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  if (!canSuggest && !canRecord && !canHear) return null;

  const release = (): void => {
    if (ticker.current) clearInterval(ticker.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (recordingFor === nameId) recordingFor = null;
  };

  const dropClip = (): void => {
    setClipUrl((url) => {
      if (url) URL.revokeObjectURL(url);
      return null;
    });
    clip.current = null;
  };

  // Leaving the page lets go of the microphone and the recording; nothing is sent.
  useEffect(
    () => () => {
      mounted.current = false;
      const rec = recorder.current;
      if (rec) {
        rec.onstop = null;
        if (rec.state !== "inactive") rec.stop();
      }
      release();
      if (clipUrl) URL.revokeObjectURL(clipUrl);
    },
    // Only on leaving; the values read are refs, or the last url (revoked by dropClip otherwise).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // The keyboard follows the thing that replaced the button that was pressed.
  useEffect(() => {
    if (phase === "recording") stopButton.current?.focus();
  }, [phase]);

  const fail = (error: unknown): void => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(Object.hasOwn(words.problem, kind) ? words.problem[kind]! : words.problem.server!);
  };

  const suggest = async (): Promise<void> => {
    if (!api.suggestSaidAs || suggesting) return;
    setSuggesting(true);
    setProblem(null);
    setSaid(null);
    try {
      const text = await api.suggestSaidAs(campaignRef, nameId);
      if (!mounted.current) return;
      if (text.trim() === "") setProblem(words.suggestNone);
      else {
        onValue(text, "suggested");
        setSaid(words.suggested);
      }
    } catch (error) {
      if (mounted.current) fail(error);
    } finally {
      if (mounted.current) setSuggesting(false);
    }
  };

  const start = async (): Promise<void> => {
    if (!type || (phase !== "idle" && phase !== "recorded") || recordingFor !== null) return;
    setProblem(null);
    setSaid(null);
    dropClip();
    setPhase("asking");
    let got: MediaStream;
    try {
      got = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (!mounted.current) return;
      setPhase("idle");
      setProblem(words.problem["mic-refused"]!);
      return;
    }
    if (!mounted.current) {
      got.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = got;
    recordingFor = nameId;
    const chunks: Blob[] = [];
    let rec: MediaRecorder;
    try {
      // About 32 kbit/s keeps ten seconds far under the API's 1 MB.
      rec = new MediaRecorder(got, { mimeType: type, audioBitsPerSecond: 32000 });
    } catch {
      release();
      setPhase("idle");
      setProblem(words.problem["bad-clip"]!);
      return;
    }
    recorder.current = rec;
    throwAway.current = false;
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = () => {
      release();
      if (throwAway.current) return;
      // Sent as plain webm or ogg: the API names the types it reads, without the codec part.
      const made = new Blob(chunks, { type: type.split(";")[0] as string });
      if (made.size === 0) {
        setPhase("idle");
        setProblem(words.problem["bad-clip"]!);
        return;
      }
      clip.current = made;
      setClipUrl(URL.createObjectURL(made));
      setPhase("recorded");
      // The record button is back in the row: the keyboard returns to the thing that started it.
      setTimeout(() => recordButton.current?.focus(), 0);
    };
    try {
      rec.start();
    } catch {
      release();
      setPhase("idle");
      setProblem(words.problem["bad-clip"]!);
      return;
    }
    setPhase("recording");
    let elapsed = 0;
    setSeconds(0);
    ticker.current = setInterval(() => {
      elapsed += 1;
      setSeconds(elapsed);
      if (elapsed >= CLIP_SECONDS) stop();
    }, 1000);
  };

  const stop = (): void => {
    const rec = recorder.current;
    if (ticker.current) clearInterval(ticker.current);
    if (rec && rec.state !== "inactive") rec.stop();
  };

  const useThis = async (): Promise<void> => {
    const made = clip.current;
    if (!made || !api.respellFromVoice) return;
    setPhase("sending");
    setProblem(null);
    try {
      const text = await api.respellFromVoice(campaignRef, nameId, made);
      if (!mounted.current) return;
      onValue(text, "heard");
      setSaid(words.filled);
      dropClip();
      setPhase("idle");
    } catch (error) {
      if (!mounted.current) return;
      fail(error);
      // The clip is still here: the DM may try again or record it again.
      setPhase("recorded");
    }
  };

  const hear = (): void => {
    if (!canHear || value.trim() === "") return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(value));
  };

  const busy = disabled || suggesting || phase === "asking" || phase === "sending";
  return (
    <div class="say-it">
      <div class="row">
        {canSuggest && phase === "idle" && (
          <button type="button" class="button secondary" disabled={busy} onClick={() => void suggest()}>
            {suggesting ? words.suggesting : words.suggest}
          </button>
        )}
        {canRecord && (phase === "idle" || phase === "asking" || phase === "recorded") && (
          <button
            ref={recordButton}
            type="button"
            class="button secondary"
            disabled={busy}
            onClick={() => void start()}
          >
            {phase === "recorded" ? words.again : phase === "asking" ? words.asking : words.sayIt}
          </button>
        )}
        {phase === "recording" && (
          <button ref={stopButton} type="button" class="button" onClick={stop}>
            {words.stop}
          </button>
        )}
        {canHear && phase === "idle" && value.trim() !== "" && (
          <button type="button" class="button secondary" disabled={busy} onClick={hear}>
            {words.hear}
          </button>
        )}
      </div>
      {phase === "recording" && (
        <p class="small" aria-hidden="true">
          {words.recording(seconds)}
        </p>
      )}
      {phase === "recorded" && clipUrl && (
        <div class="stack">
          <p class="small">{words.recorded}</p>
          {/* The recording plays here, in this browser, before anything is sent. */}
          <audio controls src={clipUrl} aria-label={words.playbackLabel(name)}>
            <a href={clipUrl}>{words.playBack}</a>
          </audio>
          <div class="row">
            <button type="button" class="button" disabled={busy} onClick={() => void useThis()}>
              {words.useThis}
            </button>
          </div>
        </div>
      )}
      {phase === "asking" && (
        <p class="small muted" role="status">
          {words.askingHint}
        </p>
      )}
      {phase === "sending" && (
        <p class="small muted" role="status">
          {words.sending}
        </p>
      )}
      {/* Said once when it starts (the ticking line above is for the eyes only). */}
      <p class="sr-only" role="status">
        {phase === "recording" ? words.startedAloud : ""}
      </p>
      {said && (
        <p class="small muted" role="status">
          {said}
        </p>
      )}
      {problem && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
      <p class="small muted">{words.how}</p>
    </div>
  );
}

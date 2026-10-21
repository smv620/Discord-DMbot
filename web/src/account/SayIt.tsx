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
  fieldId,
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
  /** The box's id, so the keyboard can go back to it once the words are in. */
  fieldId?: string;
  disabled?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [clipUrl, setClipUrl] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const clip = useRef<Blob | null>(null);
  // The playback address is kept here as well as in state, so leaving the page can let it go.
  const clipUrlRef = useRef<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const mounted = useRef(true);
  const sending = useRef(false);
  const stopButton = useRef<HTMLButtonElement>(null);
  const recordButton = useRef<HTMLButtonElement>(null);

  const type = typeof window === "undefined" ? null : pickType();
  const canSuggest = Boolean(api.suggestSaidAs);
  const canRecord = Boolean(api.respellFromVoice) && type !== null && Boolean(navigator.mediaDevices?.getUserMedia);
  const canHear =
    typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";

  const release = (): void => {
    if (ticker.current) clearInterval(ticker.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (recordingFor === nameId) recordingFor = null;
  };

  const dropClip = (): void => {
    if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
    clipUrlRef.current = null;
    setClipUrl(null);
    clip.current = null;
  };

  // Leaving the page lets go of the microphone, the recording and the voice; nothing is sent.
  useEffect(
    () => () => {
      mounted.current = false;
      const rec = recorder.current;
      if (rec) {
        rec.onstop = null;
        try {
          if (rec.state !== "inactive") rec.stop();
        } catch {
          // Leaving anyway: the microphone is let go just below.
        }
      }
      release();
      if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
      clipUrlRef.current = null;
      if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
    },
    // Everything read is a ref or a module value, so the first render's closure is the right one.
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

  /** Back onto the record button after something went wrong, so the keyboard isn't lost. */
  const backToRecord = (): void => {
    setTimeout(() => recordButton.current?.focus(), 0);
  };

  const start = async (): Promise<void> => {
    if (!type || (phase !== "idle" && phase !== "recorded")) return;
    if (recordingFor !== null && recordingFor !== nameId) {
      setProblem(words.problem.busy!);
      return;
    }
    // Taken before the permission prompt, so a second name pressed meanwhile is told to wait.
    recordingFor = nameId;
    setProblem(null);
    setSaid(null);
    // A voice still reading the box aloud would be heard by the microphone.
    if (canHear) window.speechSynthesis.cancel();
    setPhase("asking");
    let got: MediaStream;
    try {
      got = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (recordingFor === nameId) recordingFor = null;
      if (!mounted.current) return;
      setPhase(clip.current ? "recorded" : "idle");
      setProblem(words.problem["mic-refused"]!);
      backToRecord();
      return;
    }
    if (!mounted.current) {
      if (recordingFor === nameId) recordingFor = null;
      got.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = got;
    const chunks: Blob[] = [];
    let rec: MediaRecorder;
    try {
      // About 32 kbit/s keeps ten seconds far under the API's 1 MB.
      rec = new MediaRecorder(got, { mimeType: type, audioBitsPerSecond: 32000 });
    } catch {
      release();
      setPhase(clip.current ? "recorded" : "idle");
      setProblem(words.problem["bad-clip"]!);
      backToRecord();
      return;
    }
    recorder.current = rec;
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = () => {
      release();
      // Sent as plain webm or ogg: the API names the types it reads, without the codec part.
      const made = new Blob(chunks, { type: type.split(";")[0] as string });
      if (made.size === 0) {
        setPhase(clip.current ? "recorded" : "idle");
        setProblem(words.problem["bad-clip"]!);
        backToRecord();
        return;
      }
      clip.current = made;
      const url = URL.createObjectURL(made);
      clipUrlRef.current = url;
      setClipUrl(url);
      setPhase("recorded");
      backToRecord();
    };
    try {
      rec.start();
    } catch {
      release();
      setPhase(clip.current ? "recorded" : "idle");
      setProblem(words.problem["bad-clip"]!);
      backToRecord();
      return;
    }
    // The old recording is let go only now that a new one has really begun.
    if (clipUrlRef.current) {
      URL.revokeObjectURL(clipUrlRef.current);
      clipUrlRef.current = null;
      setClipUrl(null);
      clip.current = null;
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
    if (!rec || rec.state === "inactive") return;
    try {
      rec.stop();
    } catch {
      // The recorder would not stop: let go of the microphone and the lock rather than stay stuck.
      release();
      setPhase("idle");
      setProblem(words.problem["bad-clip"]!);
      backToRecord();
    }
  };

  const useThis = async (): Promise<void> => {
    const made = clip.current;
    if (!made || !api.respellFromVoice || sending.current) return;
    sending.current = true;
    setPhase("sending");
    setProblem(null);
    try {
      const text = await api.respellFromVoice(campaignRef, nameId, made);
      if (!mounted.current) return;
      onValue(text, "heard");
      setSaid(words.filled);
      dropClip();
      setPhase("idle");
      // The words are in the box: the keyboard goes there, to check and save them.
      if (fieldId) setTimeout(() => document.getElementById(fieldId)?.focus(), 0);
    } catch (error) {
      if (!mounted.current) return;
      fail(error);
      // The clip is still here: the DM may try again or record it again.
      setPhase("recorded");
    } finally {
      sending.current = false;
    }
  };

  const hear = (): void => {
    if (!canHear || value.trim() === "") return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(value));
  };

  if (!canSuggest && !canRecord && !canHear) return null;

  const busy = disabled || suggesting || phase === "asking" || phase === "sending";
  // What a screen reader is told as things change; the region is always on the page, so it is heard.
  const spoken =
    phase === "recording"
      ? words.startedAloud
      : phase === "recorded"
        ? words.recorded
        : phase === "asking"
          ? words.askingHint
          : phase === "sending"
            ? words.sending
            : (said ?? "");
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
      {phase === "asking" && <p class="small muted">{words.askingHint}</p>}
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
      {phase === "sending" && <p class="small muted">{words.sending}</p>}
      {said && phase === "idle" && <p class="small muted">{said}</p>}
      <p class="sr-only" role="status">
        {spoken}
      </p>
      <p role="alert" class={problem ? "warn small" : "visually-hidden"}>
        {problem ?? ""}
      </p>
      <p class="small muted">{words.how}</p>
    </div>
  );
}

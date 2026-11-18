/** @jsxImportSource preact */
// A PDF on the Upload lore page (#4762, behind LORE_PDF_LIVE): the DM chooses a PDF, it is sent, DMbot counts its pages and
// pictures (no AI is called, no word is read) and says what reading it will cost, and only the DM's yes starts the read. The
// file's words are never shown or kept by the page. The choices (who knows it, whose story) and the right-to-use box belong to
// the Upload lore form around this, so they are the same for every way of adding lore. The file and its count come first on the
// page and the yes comes last, after those questions, so it is never pressed before they are answered: the form holds the state
// (`useLorePdf`) and draws the two parts where they belong.
import { useEffect, useRef, useState } from "preact/hooks";

import { lorePdfWords as pdfWords } from "../content/lore-pdf-words";
import { lorePage as words } from "../content/lore-words";
import { type AccountApi, ApiError, type LorePdf, type LoreUpload } from "./api";
import WorkProgress from "./WorkProgress";

/** The most the website sends (core `web/lore_pdf.MAX_BYTES`): change them together. */
export const PDF_MAX_BYTES = 50 * 1024 * 1024;
/** Sending or counting quiet this long says it is taking longer than usual (the bot's own limit is longer). */
const SLOW_AFTER_S = 120;

type Step = "idle" | "sending" | "counting" | "reading";

const nowS = (): number => Math.floor(Date.now() / 1000);

export interface LorePdfState {
  pdf: LorePdf | null;
  step: Step;
  began: number;
  problem: string;
  note: string;
  setProblem: (words: string) => void;
  choose: (file: File | undefined) => Promise<void>;
  yes: (input: { choice: "world" | "spoilers" | "smart"; speaker: string; rights: boolean }) => Promise<void>;
  never: () => Promise<void>;
  /** Back to a clean part: after an upload ends, and when the DM changes the way of adding lore. */
  reset: () => void;
}

export function useLorePdf(
  api: AccountApi,
  ref: string | null,
  enabled: boolean,
  onStarted: (upload: LoreUpload) => void,
  onBusy: () => void,
): LorePdfState {
  const [pdf, setPdf] = useState<LorePdf | null>(null);
  const [step, setStep] = useState<Step>("idle");
  const [began, setBegan] = useState(0);
  const [problem, setProblem] = useState("");
  const [note, setNote] = useState("");
  // A second press in the same tick (before the buttons are disabled by the next render) must not send twice.
  const inFlight = useRef(false);
  const touched = useRef(false); // the DM has chosen a file since this page loaded

  // A file already waiting (sent before a reload) is shown again, so a counted PDF is not lost.
  useEffect(() => {
    if (!enabled || !ref || !api.lorePdf) return;
    let live = true;
    void api
      .lorePdf(ref)
      .then((got) => live && !touched.current && !inFlight.current && got.state !== "none" && got.state !== "reading" && setPdf(got))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, ref, enabled]);

  const fail = (err: unknown) => setProblem(pdfWords.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);

  // The count: the bot reads the file's pages and pictures and writes three numbers back; nothing is sent to an AI.
  async function count() {
    if (!api.lorePdfCount || !ref) {
      setStep("idle");
      return;
    }
    setStep("counting");
    setBegan(nowS());
    try {
      const got = await api.lorePdfCount(ref);
      if (got.state === "none" || got.state === "staged") {
        setPdf(null);
        setProblem(pdfWords.gone);
      } else setPdf(got);
    } catch (err) {
      // (the bot says the file is gone when the bucket's one-day rule took it)
      if (err instanceof ApiError && err.kind === "not-found") setProblem(pdfWords.gone);
      else fail(err);
      setPdf(null);
    } finally {
      setStep("idle");
    }
  }

  async function choose(file: File | undefined) {
    setProblem("");
    setNote("");
    if (!file || !api.lorePdfSend || !ref || inFlight.current) return;
    if (file.size > PDF_MAX_BYTES || file.size === 0) {
      setProblem(pdfWords.problems["bad-file"] ?? words.genericProblem);
      return;
    }
    inFlight.current = touched.current = true;
    setPdf(null);
    setStep("sending");
    setBegan(nowS());
    try {
      setPdf(await api.lorePdfSend(ref, file));
      await count();
    } catch (err) {
      fail(err);
      setStep("idle");
    } finally {
      inFlight.current = false;
    }
  }

  async function yes(input: { choice: "world" | "spoilers" | "smart"; speaker: string; rights: boolean }) {
    if (!api.lorePdfGo || !ref || !pdf?.ref) return;
    if (!input.rights) return setProblem(pdfWords.rightsNeeded);
    if (inFlight.current) return;
    inFlight.current = true;
    setStep("reading");
    setProblem("");
    try {
      const upload = await api.lorePdfGo(ref, { ref: pdf.ref, ...input });
      // The file is the read's now: nothing of it stays on the page, so the next upload starts clean.
      setPdf(null);
      setStep("idle");
      onStarted(upload);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "slow-down") onBusy();
      else fail(err);
      setStep("idle");
    } finally {
      inFlight.current = false;
    }
  }

  async function never() {
    if (!ref) return;
    setProblem("");
    try {
      await api.lorePdfDrop?.(ref);
      setNote(pdfWords.dropped);
    } catch {
      // (a file that could not be dropped goes by itself within a day)
    }
    setPdf(null);
  }

  const reset = () => {
    setPdf(null);
    setStep("idle");
    setProblem("");
    setNote("");
    touched.current = false;
  };

  return { pdf, step, began, problem, note, setProblem, choose, yes, never, reset };
}

/** The file, the wait while it is sent and counted, and what the count found. Drawn first. */
export function LorePdfPicker({ s }: { s: LorePdfState }) {
  const busy = s.step !== "idle";
  const counted = s.pdf?.state === "estimated" && typeof s.pdf.pages === "number";
  const slow = busy && s.began > 0;
  return (
    <div class="stack">
      <label for="lore-pdf">{pdfWords.label}</label>
      <input
        id="lore-pdf"
        type="file"
        accept="application/pdf,.pdf"
        disabled={busy}
        aria-describedby="lore-pdf-help"
        onChange={(e) => {
          const input = e.target as HTMLInputElement;
          void s.choose(input.files?.[0]);
        }}
      />
      <p id="lore-pdf-help" class="small">{pdfWords.help}</p>
      {(s.step === "sending" || s.step === "counting") && <PdfWait step={s.step} began={s.began} slow={slow} />}
      {s.note && <p role="status">{s.note}</p>}
      {counted && s.pdf && (
        <p role="status">{pdfWords.counted(s.pdf.pages ?? 0, s.pdf.pictures ?? 0, s.pdf.estimateWords ?? null)}</p>
      )}
    </div>
  );
}

/** The wait panel for sending and counting: its clock ticks, and past two minutes it says it is slow. */
function PdfWait({ step, began, slow }: { step: Step; began: number; slow: boolean }) {
  const [now, setNow] = useState(nowS());
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    tick.current = setInterval(() => setNow(nowS()), 5000);
    return () => {
      if (tick.current) clearInterval(tick.current);
    };
  }, []);
  const stalled = slow && now - began > SLOW_AFTER_S;
  return (
    <WorkProgress
      stage={step === "sending" ? pdfWords.sending : pdfWords.counting}
      startedAt={began}
      updatedAt={began}
      serverNow={null}
      state={stalled ? "stalled" : "running"}
      progressUnknown
      whatToDo={stalled ? pdfWords.slow : undefined}
    />
  );
}

/** "Read this PDF" and "Never mind": drawn last, after the questions and the right-to-use box. */
export function LorePdfButtons({ s, onYes }: { s: LorePdfState; onYes: () => void }) {
  const busy = s.step !== "idle";
  const counted = s.pdf?.state === "estimated" && typeof s.pdf.pages === "number";
  if (!counted) return null;
  return (
    <p>
      <button type="button" class="button" disabled={busy} onClick={onYes}>{pdfWords.read}</button>{" "}
      <button type="button" class="button secondary" disabled={busy} onClick={() => void s.never()}>{pdfWords.never}</button>
    </p>
  );
}

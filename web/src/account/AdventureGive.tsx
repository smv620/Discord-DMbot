/** @jsxImportSource preact */
// "Give a file" under "Your adventure" (#3694 slice D, behind ADVENTURE_LIVE; core #5309): the DM picks one file (PDF, Word or text), the
// page sends it as it is, and the bot reads its text and keeps it for this campaign only. The page then polls the one give, showing its stage,
// how long it has run and when it last moved (the shared wait panel), and says plainly what happened. Only the file's name, size and
// counts are ever shown back, never its words. The DM said they have the right to use the material when they turned Shared story on (the
// bot checks it is on record); nothing here is a second yes. Stops watching when the page goes away or the campaign changes.
import { useEffect, useRef, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AccountApi, ApiError, type AdventureGive } from "./api";
import WorkProgress from "./WorkProgress";

const PDF_MAX = 50 * 1024 * 1024;
const OTHER_MAX = 10 * 1024 * 1024;
const FIRST_MS = 600;
const EVERY_MS = 1500;
const LOOKS = 80; // about two minutes: a long PDF takes a while to read

const kindOf = (name: string): "pdf" | "other" | null => {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  if (ext === "pdf") return "pdf";
  return ["docx", "txt", "md", "text"].includes(ext) ? "other" : null;
};

/** The words for a file the page can already turn away (the bot checks again): "" when it can be sent. */
export function whyNot(file: { name: string; size: number }): string {
  const kind = kindOf(file.name);
  if (kind === null) return w.giveWrong;
  if (file.size === 0) return w.giveEmpty;
  if (file.name.length > 200) return w.giveName;
  const max = kind === "pdf" ? PDF_MAX : OTHER_MAX;
  return file.size > max ? w.giveBig(max / (1024 * 1024)) : "";
}

export default function AdventureGive({ api, refId, onKept }: { api: AccountApi; refId: string; onKept: () => void }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<AdventureGive | null>(null);
  const [said, setSaid] = useState("");
  const epoch = useRef(0);
  const sending = useRef(false);
  const statusBox = useRef<HTMLParagraphElement>(null);
  const fileBox = useRef<HTMLInputElement>(null);
  const waitBox = useRef<HTMLDivElement>(null);

  // Leaving the page or changing campaign ends the watching.
  useEffect(() => {
    return () => {
      epoch.current += 1;
      sending.current = false;
    };
  }, [api, refId]);
  useEffect(() => {
    if (busy) waitBox.current?.focus();
  }, [busy]);

  async function give(): Promise<void> {
    if (!api.adventureGive || !api.adventureGiving || sending.current) return;
    if (!file) return setProblem(w.giveNone);
    const refused = whyNot(file);
    if (refused) return setProblem(refused);
    sending.current = true;
    const mine = epoch.current;
    const here = (): boolean => mine === epoch.current;
    setProblem("");
    setSaid("");
    setSeen(null);
    setBusy(true);
    const end = (text: string, bad: boolean): void => {
      if (!here()) return;
      setBusy(false);
      if (bad) setProblem(text);
      else {
        setSaid(text);
        setFile(null);
        setOpen(false);
      }
      onKept(); // (the files are read again, whatever happened)
      setTimeout(() => (bad ? fileBox.current : statusBox.current)?.focus(), 0);
    };
    try {
      let id: string;
      try {
        id = (await api.adventureGive(refId, file, file.name)).give;
      } catch (error) {
        const kind = error instanceof ApiError ? error.kind : "network";
        if (here()) {
          setBusy(false);
          setProblem(w.giveRefused[kind] ?? w.giveRefused.server ?? "");
        }
        return;
      }
      let errors = 0;
      for (let i = 0; i < LOOKS; i += 1) {
        await new Promise((ok) => setTimeout(ok, i === 0 ? FIRST_MS : EVERY_MS));
        if (!here()) return;
        try {
          const got = await api.adventureGiving(refId, id);
          errors = 0;
          if (!here()) return;
          setSeen(got);
          if (got.state === "done") return end(w.given(got.name || file.name), false);
          if (got.state === "failed") return end(w.giveFailed[got.error] ?? w.giveFailed.failed ?? "", true);
        } catch (error) {
          // A job that is gone or a person signed out is not a blip: stop at once.
          if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "signed-out")) return end(w.giveSlow, true);
          errors += 1; // (one failed look is not a failed give: it is queued and will probably finish)
          if (errors >= 3) return end(w.giveSlow, true);
        }
      }
      end(w.giveSlow, true);
    } finally {
      sending.current = false;
    }
  }

  if (!api.adventureGive || !api.adventureGiving) return null;
  return (
    <div class="stack">
      {!open && !busy && (
        <button type="button" class="button secondary" onClick={() => {
            setOpen(true);
            setSaid("");
            setProblem("");
          }}>
          {w.give}
        </button>
      )}
      {said && (
        <p role="status" tabIndex={-1} ref={statusBox}>
          {said}
        </p>
      )}
      {(open || busy) && (
        <div class="stack">
          <label class="field" for={`adv-file-${refId}`}>
            {w.giveFile}
          </label>
          <input
            id={`adv-file-${refId}`}
            type="file"
            ref={fileBox}
            accept=".pdf,.docx,.txt,.md,.text"
            disabled={busy}
            aria-describedby={`adv-file-help-${refId}`}
            onChange={(e) => {
              const picked = (e.target as HTMLInputElement).files?.[0] ?? null;
              setFile(picked);
              setProblem(picked ? whyNot(picked) : "");
            }}
          />
          <p class="small muted" id={`adv-file-help-${refId}`}>
            {w.giveHelp}
          </p>
          {problem && (
            <p class="warn" role="alert">
              {problem}
            </p>
          )}
          {busy && (
            <div ref={waitBox} tabIndex={-1}>
              <WorkProgress
                stage={w.stagesGive[seen?.stage ?? "waiting"] ?? w.giving}
                startedAt={seen?.createdAt}
                updatedAt={seen?.updatedAt}
                serverNow={seen?.serverNow}
                state="running"
              />
            </div>
          )}
          <div class="row">
            <button type="button" class="button" disabled={busy} onClick={() => void give()}>
              {w.giveSend}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => {
                setOpen(false);
                setFile(null);
                setProblem("");
              }}>
              {w.giveCancel}
            </button>
          </div>
        </div>
      )}
      {!open && !busy && problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

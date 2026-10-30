/** @jsxImportSource preact */
// Recap style (#2985): the DM's example recaps and standing notes, each change queued for the bot and followed to its
// end (the shared wait panel says the stage, the clock and when it last moved). The examples are the DM's own words
// and are shown only to a campaign's DM; the API says so too. Nothing is kept on this page after it is sent.
import { useEffect, useRef, useState } from "preact/hooks";

import { recapStylePage as words, STYLE_EXAMPLE_WORDS, STYLE_MAX_EXAMPLES, STYLE_NOTE_CHARS } from "../content/recap-style-words";
import { type AccountApi, ApiError, type RecapStyle, type RecapStyleAction } from "./api";
import WorkProgress from "./WorkProgress";

/** The campaign's address, read from `/account/recap-style/<server>.<campaign>`. */
export function campaignFromPath(path: string): string | null {
  const match = /^\/account\/recap-style\/([^/]+)\/?$/.exec(path);
  return match ? decodeURIComponent(match[1] ?? "") : null;
}

const POLL_MS = 2000;
const POLL_MAX_MS = 20_000;

type Change = RecapStyleAction["action"];

function problemFor(err: unknown): string {
  return words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem;
}

const wordCount = (text: string): number => (text.trim() === "" ? 0 : text.trim().split(/\s+/).length);

export default function RecapStyleView({ api, path }: { api: AccountApi; path: string }) {
  const ref = campaignFromPath(path);
  const [style, setStyle] = useState<RecapStyle | null>(null);
  const [example, setExample] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [open, setOpen] = useState<{ id: string; action: Change; at: RecapStyleAction | null } | null>(null);
  const [stopped, setStopped] = useState(false);
  const alertBox = useRef<HTMLParagraphElement>(null);
  const idle = useRef(0);
  const seen = useRef(-1);

  const load = async (): Promise<void> => {
    if (!ref || !api.recapStyle) return;
    try {
      setStyle(await api.recapStyle(ref));
    } catch (err) {
      setProblem(problemFor(err));
    }
  };
  useEffect(() => {
    void load();
  }, [api, ref]);

  // Follow the one change that is open until it is done or failed, asking less often while nothing moves.
  useEffect(() => {
    if (!ref || !open || !api.recapStyleAction) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async (): Promise<void> => {
      try {
        const got = await api.recapStyleAction?.(ref, open.id);
        if (!live || !got) return;
        idle.current = got.updatedAt === seen.current ? idle.current + 1 : 0;
        seen.current = got.updatedAt;
        setOpen((o) => (o && o.id === open.id ? { ...o, at: got } : o));
        if (got.state === "done") {
          void load();
          return;
        }
        if (got.state === "failed") return;
        timer = setTimeout(look, Math.min(POLL_MS * 2 ** Math.min(idle.current, 3), POLL_MAX_MS));
      } catch {
        if (live) setStopped(true);
      }
    };
    timer = setTimeout(look, POLL_MS);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, ref, open?.id]);

  useEffect(() => {
    if (problem) alertBox.current?.focus();
  }, [problem]);

  if (!ref) return <p>{words.notFound}</p>;

  const send = async (action: Change, arg = ""): Promise<boolean> => {
    if (!api.changeRecapStyle || !ref) return false;
    setBusy(true);
    setProblem("");
    try {
      const got = await api.changeRecapStyle(ref, action, arg);
      seen.current = -1;
      idle.current = 0;
      setStopped(false);
      setOpen({ id: got.id, action, at: null });
      return true;
    } catch (err) {
      setProblem(problemFor(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const failed = open?.at?.state === "failed";
  const done = open?.at?.state === "done";
  const examples = style?.examples ?? [];
  const full = examples.length >= STYLE_MAX_EXAMPLES;
  const words600 = wordCount(example);

  return (
    <div class="stack">
      <p>
        <a href={`/account/campaigns/${encodeURIComponent(ref)}`}>{words.back}</a>
      </p>
      <p>{words.lead}</p>
      {open && (
        <WorkProgress
          stage={done ? words.doneNote : open.at?.state === "working" ? words.working : words.waiting}
          startedAt={open.at?.createdAt ?? null}
          updatedAt={open.at?.updatedAt ?? null}
          serverNow={open.at?.serverNow ?? null}
          state={failed ? "failed" : done ? "done" : "running"}
          waiting={open.at?.state !== "working" && !done && !failed}
          frozen={stopped}
          reason={failed ? (words.failedReasons[open.at?.error ?? ""] ?? words.failedFallback) : null}
        />
      )}
      {problem && (
        <p role="alert" ref={alertBox} tabIndex={-1}>
          {problem}
        </p>
      )}

      <section class="stack" aria-labelledby="rs-examples">
        <h2 id="rs-examples">{words.examplesHeading}</h2>
        <p>{style === null ? words.loading : examples.length === 0 ? words.usingDefaults : words.usingOwn(examples.length)}</p>
        <ol class="stack">
          {examples.map((e, i) => (
            <li key={e.id} class="stack">
              <p class="small">{e.text.length > 400 ? `${e.text.slice(0, 400)}…` : e.text}</p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => void send("remove_example", e.id)}>
                {words.remove(i + 1)}
              </button>
            </li>
          ))}
        </ol>
        {examples.length > 0 && (
          <p>
            <button type="button" class="button secondary" disabled={busy} onClick={() => void send("use_defaults")}>
              {words.useDefaults}
            </button>{" "}
            <span class="small">{words.useDefaultsNote}</span>
          </p>
        )}
        {full ? (
          <p>{words.exampleFull}</p>
        ) : (
          <form
            class="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              if (example.trim() === "") return;
              if (words600 > STYLE_EXAMPLE_WORDS) return setProblem(words.tooManyWords);
              if (await send("add_example", example.trim())) setExample("");
            }}
          >
            <label for="rs-example">{words.exampleLabel}</label>
            <textarea id="rs-example" rows={8} value={example} onInput={(e) => setExample((e.target as HTMLTextAreaElement).value)} aria-describedby="rs-example-help" />
            <p id="rs-example-help" class="small">
              {words.exampleHelp} {words.count(words600)}
            </p>
            <p>
              <button type="submit" class="button" disabled={busy}>
                {busy ? words.sending : words.addExample}
              </button>
            </p>
          </form>
        )}
      </section>

      <section class="stack" aria-labelledby="rs-notes">
        <h2 id="rs-notes">{words.notesHeading}</h2>
        {style !== null && style.notes.length === 0 && <p>{words.notesNone}</p>}
        <ol class="stack">
          {(style?.notes ?? []).map((n, i) => (
            <li key={n.id}>
              {n.text}{" "}
              <button type="button" class="button secondary" disabled={busy} onClick={() => void send("remove_note", n.id)}>
                {words.removeNote(i + 1)}
              </button>
            </li>
          ))}
        </ol>
        <form
          class="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            if (note.trim() === "") return;
            if (await send("add_note", note.trim())) setNote("");
          }}
        >
          <label for="rs-note">{words.noteLabel}</label>
          <input id="rs-note" type="text" maxLength={STYLE_NOTE_CHARS} value={note} onInput={(e) => setNote((e.target as HTMLInputElement).value)} aria-describedby="rs-note-help" />
          <p id="rs-note-help" class="small">
            {words.noteHelp}
          </p>
          <p>
            <button type="submit" class="button" disabled={busy}>
              {busy ? words.sending : words.addNote}
            </button>
          </p>
        </form>
      </section>
    </div>
  );
}

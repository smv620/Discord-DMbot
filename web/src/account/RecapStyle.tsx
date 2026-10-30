/** @jsxImportSource preact */
// Recap style (#2985): the DM's example recaps and standing notes, each change queued for the bot and followed to its
// end (the shared wait panel says the stage, the clock and when it last moved). The examples are the DM's own words
// and are shown only to a campaign's DM; the API says so too. Nothing is kept on this page after it is sent.
import { useEffect, useRef, useState } from "preact/hooks";

import { recapStylePage as words, STYLE_EXAMPLE_WORDS, STYLE_MAX_EXAMPLES, STYLE_MAX_NOTES, STYLE_NOTE_CHARS } from "../content/recap-style-words";
import { type AccountApi, ApiError, type RecapStyle, type RecapStyleAction } from "./api";
import WorkProgress from "./WorkProgress";

/** The campaign's address, read from `/account/recap-style/<server>.<campaign>`. */
export function campaignFromPath(path: string): string | null {
  const match = /^\/account\/recap-style\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null; // (a broken address is "not found", not a blank page)
  }
}

const POLL_MS = 2000;
const POLL_MAX_MS = 20_000;
/** A change nobody has touched for this long reads as stalled (the door puts a taken one back at ten minutes). */
const STALLED_S = 300;

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
  // A delete that is waiting for "Yes": nothing is sent until then, because a deleted example cannot come back.
  const [ask, setAsk] = useState<{ action: Change; arg: string; text: string; yes: string } | null>(null);
  const alertBox = useRef<HTMLParagraphElement>(null);
  const idle = useRef(0);
  const errors = useRef(0);
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
        errors.current = 0;
        idle.current = got.updatedAt === seen.current ? idle.current + 1 : 0;
        seen.current = got.updatedAt;
        setOpen((o) => (o && o.id === open.id ? { ...o, at: got } : o));
        if (got.state === "done") {
          // What was typed is cleared only now, so a change that fails loses none of a long paste.
          if (open.action === "add_example") setExample("");
          if (open.action === "add_note") setNote("");
          void load();
          return;
        }
        if (got.state === "failed") return;
        timer = setTimeout(look, Math.min(POLL_MS * 2 ** Math.min(idle.current, 3), POLL_MAX_MS));
      } catch {
        if (!live) return;
        // One lost answer is not the end: try again a few times before saying the page has stopped looking.
        errors.current += 1;
        if (errors.current >= 3) setStopped(true);
        else timer = setTimeout(look, POLL_MS * 2 ** errors.current);
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
      errors.current = 0;
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
  // One change at a time: the controls rest until the one in flight has finished, so its end is never overwritten.
  const pending = open !== null && !failed && !done && !stopped;
  // Quiet this long, waiting for the bot or working, is a stall (the panel's `waiting` is for waiting on the person).
  const stalled = open?.at ? open.at.serverNow - open.at.updatedAt > STALLED_S : false;
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
          stage={(done ? words.doneNote : open.at?.state === "working" ? words.working : words.waiting)[open.action] ?? words.genericProblem}
          startedAt={open.at?.createdAt ?? null}
          updatedAt={open.at?.updatedAt ?? null}
          serverNow={open.at?.serverNow ?? null}
          state={failed ? "failed" : done ? "done" : stalled ? "stalled" : "running"}
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
        <p class="small">{words.examplesIntro}</p>
        <ol class="stack">
          {examples.map((e, i) => (
            <li key={e.id} class="stack">
              {e.text.length > 400 ? (
                <details>
                  <summary class="small">
                    {e.text.slice(0, 400)}… ({words.exampleWords(wordCount(e.text))}) {words.showAll}
                  </summary>
                  <p class="small">{e.text}</p>
                </details>
              ) : (
                <p class="small">
                  {e.text} ({words.exampleWords(wordCount(e.text))})
                </p>
              )}
              <button
                type="button"
                class="button secondary"
                disabled={busy || pending}
                aria-label={`${words.remove(i + 1)}: ${e.text.slice(0, 30)}`}
                onClick={() => setAsk({ action: "remove_example", arg: e.id, text: words.removeAsk(i + 1), yes: words.removeYes })}
              >
                {words.remove(i + 1)}
              </button>
            </li>
          ))}
        </ol>
        {examples.length > 0 && (
          <p>
            <button type="button" class="button secondary" disabled={busy || pending} onClick={() => setAsk({ action: "use_defaults", arg: "", text: words.deleteAsk, yes: words.deleteYes })}>
              {words.useDefaults}
            </button>
          </p>
        )}
        {ask && (
          <div class="stack" role="group" aria-label={ask.text}>
            <p role="alert">{ask.text}</p>
            <p>
              <button
                type="button"
                class="button"
                disabled={busy || pending}
                onClick={async () => {
                  const { action, arg } = ask;
                  setAsk(null);
                  await send(action, arg);
                }}
              >
                {ask.yes}
              </button>{" "}
              <button type="button" class="button secondary" onClick={() => setAsk(null)}>
                {words.keep}
              </button>
            </p>
          </div>
        )}
        {full ? (
          <p>{words.exampleFull}</p>
        ) : (
          <form
            class="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              if (example.trim() === "") return;
              if (words600 > STYLE_EXAMPLE_WORDS) return setProblem(words.tooManyWords(words600));
              await send("add_example", example.trim());
            }}
          >
            <label for="rs-example">{words.exampleLabel}</label>
            <textarea id="rs-example" rows={8} value={example} onInput={(e) => setExample((e.target as HTMLTextAreaElement).value)} aria-describedby="rs-example-help" />
            <p id="rs-example-help" class="small">
              {words.exampleHelp} {words.count(words600)}
            </p>
            {words600 > STYLE_EXAMPLE_WORDS && <p role="alert">{words.tooManyWords(words600)}</p>}
            <p>
              <button type="submit" class="button" disabled={busy || pending || words600 > STYLE_EXAMPLE_WORDS}>
                {busy ? words.sending : words.addExample}
              </button>
            </p>
          </form>
        )}
      </section>

      <section class="stack" aria-labelledby="rs-notes">
        <h2 id="rs-notes">{words.notesHeading}</h2>
        <p class="small">{words.notesIntro}</p>
        {style !== null && style.notes.length === 0 && <p>{words.notesNone}</p>}
        {style !== null && style.notes.length >= STYLE_MAX_NOTES && <p>{words.notesFull}</p>}
        <ol class="stack">
          {(style?.notes ?? []).map((n, i) => (
            <li key={n.id}>
              {n.text}{" "}
              <button type="button" class="button secondary" disabled={busy || pending} aria-label={`${words.removeNote(i + 1)}: ${n.text.slice(0, 30)}`} onClick={() => void send("remove_note", n.id)}>
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
            await send("add_note", note.trim());
          }}
        >
          <label for="rs-note">{words.noteLabel}</label>
          <input id="rs-note" type="text" maxLength={STYLE_NOTE_CHARS} value={note} onInput={(e) => setNote((e.target as HTMLInputElement).value)} aria-describedby="rs-note-help" />
          <p id="rs-note-help" class="small">
            {words.noteHelp}
          </p>
          <p>
            <button type="submit" class="button" disabled={busy || pending}>
              {busy ? words.sending : words.addNote}
            </button>
          </p>
        </form>
      </section>
    </div>
  );
}

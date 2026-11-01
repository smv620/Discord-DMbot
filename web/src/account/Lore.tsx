/** @jsxImportSource preact */
// Upload lore (#3022): paste, a text file or a link, whose backstory it is, how it is known, the right-to-use box, then the
// wait (stage, clock, last progress: the shared panel) and where to check what DMbot found. The words are sent once and never
// shown back; the page keeps nothing of them. Only a campaign's DM gets a page; the door says so too.
import { useEffect, useRef, useState } from "preact/hooks";

import { lorePage as words, LORE_TEXT_MAX } from "../content/lore-words";
import { stageWords } from "../content/work-progress";
import { type AccountApi, ApiError, type LoreInput, type LoreUpload, type NameRow } from "./api";
import WorkProgress from "./WorkProgress";
import AddAll from "./AddAll";

/** The campaign's address, read from `/account/lore/<server>.<campaign>`. */
export function campaignFromPath(path: string): string | null {
  const match = /^\/account\/lore\/([^/]+)\/?$/.exec(path);
  return match ? decodeURIComponent(match[1] ?? "") : null;
}

const POLL_MS = 3000;
const POLL_MAX_MS = 30_000;
/** A taken upload quiet this long reads as stalled (the door puts it back at ten minutes). */
const STALLED_S = 300;

type Source = LoreInput["source"];
type Known = LoreInput["choice"];

function problemFor(err: unknown): string {
  return words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem;
}

export default function Lore({ api, path }: { api: AccountApi; path: string }) {
  const ref = campaignFromPath(path);
  const [source, setSource] = useState<Source>("paste");
  const [text, setText] = useState("");
  const [link, setLink] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileProblem, setFileProblem] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [known, setKnown] = useState<Known>("world");
  const [rights, setRights] = useState(false);
  const [people, setPeople] = useState<NameRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [upload, setUpload] = useState<LoreUpload | null>(null);
  const [opened, setOpened] = useState(false);
  const [stopped, setStopped] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alertBox = useRef<HTMLParagraphElement>(null);
  const idle = useRef(0);
  const seen = useRef(-1);

  // The characters for "Whose backstory is this?": the people the story has named, as the Names page lists them.
  useEffect(() => {
    if (!ref || !api.allNames) return;
    let live = true;
    void api
      .allNames(ref, { q: "", kind: "player_character,npc", sort: "name", page: 1, size: 200 })
      .then((page) => live && setPeople(page.names.filter((n) => !n.unnamed)))
      .catch(() => live && setPeople([]));
    return () => {
      live = false;
    };
  }, [api, ref]);

  // An upload already open (or finished in the last day) is shown, so a reload does not lose it.
  useEffect(() => {
    if (!ref || !api.loreRead) return;
    let live = true;
    void api
      .loreRead(ref)
      .then((got) => {
        if (live && got) {
          setUpload(got);
          setOpened(got.state === "waiting" || got.state === "working");
        }
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, ref]);

  const open = upload !== null && (upload.state === "waiting" || upload.state === "working");
  useEffect(() => {
    if (!ref || !open || !upload || !api.loreRead) return;
    let live = true;
    const look = async () => {
      try {
        const got = await api.loreRead?.(ref, upload.id);
        if (!live) return;
        if (!got) {
          setStopped(true);
          return;
        }
        idle.current = got.updatedAt === seen.current ? idle.current + 1 : 0;
        seen.current = got.updatedAt;
        setUpload(got);
        if (got.state === "waiting" || got.state === "working") {
          timer.current = setTimeout(look, Math.min(POLL_MS * 2 ** Math.min(idle.current, 4), POLL_MAX_MS));
        }
      } catch {
        if (live) setStopped(true);
      }
    };
    timer.current = setTimeout(look, POLL_MS);
    return () => {
      live = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [api, ref, open, upload?.id]);

  // A refusal sits below the box on a phone: bring it into view.
  useEffect(() => {
    if (problem) alertBox.current?.focus();
  }, [problem]);

  if (!ref) return <p>{words.notFound}</p>;

  async function chooseFile(file: File | undefined) {
    setFileProblem("");
    setFileName("");
    setText("");
    if (!file) return;
    try {
      const read = await file.text();
      if (read.includes("\u0000")) throw new Error("binary");
      if (read.length > LORE_TEXT_MAX) {
        setFileProblem(words.fileTooLong);
        return;
      }
      setText(read);
      setFileName(file.name);
    } catch {
      setFileProblem(words.fileUnreadable);
    }
  }

  const ready = source === "link" ? link.trim() !== "" : text.trim() !== "";

  async function send(e: Event) {
    e.preventDefault();
    if (!api.loreAdd || !ref) return;
    if (!ready) return setProblem(words.nothingYet);
    if (!rights) return setProblem(words.rightsNeeded);
    setBusy(true);
    setProblem("");
    try {
      const input: LoreInput = { source, choice: known, speaker, rights, ...(source === "link" ? { link: link.trim() } : { text }) };
      const got = await api.loreAdd(ref, input);
      seen.current = -1;
      idle.current = 0;
      setStopped(false);
      setOpened(false);
      setUpload(got);
      setRights(false); // (the words stay until it is done, so a failure loses none of a long paste)
    } catch (err) {
      if (err instanceof ApiError && err.kind === "slow-down") {
        const got = await api.loreRead?.(ref).catch(() => null);
        if (got) {
          seen.current = -1;
          idle.current = 0;
          setStopped(false);
          setOpened(true);
          setUpload(got);
        }
      } else setProblem(problemFor(err));
    } finally {
      setBusy(false);
    }
  }

  const back = (
    <p>
      <a href={`/account/campaigns/${encodeURIComponent(ref)}`}>{words.back}</a>
    </p>
  );

  if (upload) {
    const failed = upload.state === "failed";
    const done = upload.state === "done";
    const quiet = upload.serverNow - upload.updatedAt;
    // Quiet this long, waiting for the bot or working, is a stall (the panel's `waiting` is for waiting on the person, not on DMbot).
    const state = failed ? "failed" : done ? "done" : quiet > STALLED_S ? "stalled" : "running";
    return (
      <div class="stack">
        {back}
        {opened && open && <p>{words.openNote}</p>}
        <WorkProgress
          stage={stageWords(words.stages, upload.stage)}
          done={upload.total > 0 ? upload.done : null}
          total={upload.total > 0 ? upload.total : null}
          unit={words.unit}
          startedAt={upload.createdAt}
          updatedAt={upload.updatedAt}
          serverNow={upload.serverNow}
          state={state}
          frozen={stopped}
          reason={failed ? (words.failedReasons[upload.error] ?? words.failedFallback) : null}
        />
        {done && (
          <div class="stack">
            <p role="status">{upload.namesFound + upload.linksFound === 0 ? words.foundNothing : words.found(upload.namesFound, upload.linksFound)}</p>
            {upload.namesFound + upload.linksFound > 0 && (
              <>
                {upload.choice === "smart" && <p>{words.guessed}</p>}
                <p>{words.nextLead}</p>
                <ul class="plain-list">
                  <li><a href={`/account/names/${encodeURIComponent(ref)}`}>{words.checkNames}</a></li>
                  <li><a href={`/account/web/${encodeURIComponent(ref)}`}>{words.seeGraph}</a></li>
                </ul>
              </>
            )}
          </div>
        )}
        {done && (
          <AddAll
            api={api}
            campaign={ref}
            upload={upload}
            onChanged={() => {
              // The waiting counts changed: read the upload again, so the buttons show what is left.
              void api.loreRead?.(ref, upload.id).then((got) => got && setUpload(got)).catch(() => undefined);
            }}
          />
        )}
        {(done || failed) && (
          <button
            type="button"
            class="button secondary"
            onClick={() => {
              if (done) {
                setText("");
                setLink("");
                setFileName("");
              }
              setUpload(null);
              setProblem("");
            }}
          >
            {done ? words.again : words.retry}
          </button>
        )}
      </div>
    );
  }

  return (
    <form class="stack" onSubmit={send} noValidate>
      {back}
      <p>{words.lead}</p>
      <fieldset class="stack">
        <legend>{words.whereLegend}</legend>
        {(["paste", "file", "link"] as const).map((s) => (
          <label key={s} class="choice">
            <input type="radio" name="lore-source" value={s} checked={source === s} onChange={() => { setSource(s); setProblem(""); }} /> {words.sources[s]}
          </label>
        ))}
      </fieldset>
      {source === "paste" && (
        <div class="stack">
          <label for="lore-text">{words.pasteLabel}</label>
          <textarea id="lore-text" rows={8} maxLength={LORE_TEXT_MAX} value={text} onInput={(e) => setText((e.target as HTMLTextAreaElement).value)} aria-describedby="lore-text-help" />
          <p id="lore-text-help" class="small">{words.pasteHelp} {words.count(text.length)}</p>
        </div>
      )}
      {source === "file" && (
        <div class="stack">
          <label for="lore-file">{words.fileLabel}</label>
          <input id="lore-file" type="file" accept=".txt,.md,text/plain,text/markdown" aria-describedby="lore-file-help" onChange={(e) => void chooseFile((e.target as HTMLInputElement).files?.[0])} />
          <p id="lore-file-help" class="small">{words.fileHelp}</p>
          {fileName && <p role="status">{words.fileChosen(fileName)}</p>}
          {fileProblem && <p role="alert">{fileProblem}</p>}
        </div>
      )}
      {source === "link" && (
        <div class="stack">
          <label for="lore-link">{words.linkLabel}</label>
          <input id="lore-link" type="url" inputMode="url" value={link} onInput={(e) => setLink((e.target as HTMLInputElement).value)} aria-describedby="lore-link-help" />
          <p id="lore-link-help" class="small">{words.linkHelp}</p>
        </div>
      )}
      <div class="stack">
        <label for="lore-speaker">{words.speakerLabel}</label>
        <select id="lore-speaker" value={speaker} onChange={(e) => setSpeaker((e.target as HTMLSelectElement).value)} aria-describedby="lore-speaker-help">
          <option value="">{words.speakerNone}</option>
          {(people ?? []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <p id="lore-speaker-help" class="small">{people === null ? words.speakerLoading : words.speakerHelp}</p>
      </div>
      <fieldset class="stack">
        <legend>{words.knownLegend}</legend>
        {(["world", "spoilers", "smart"] as const).map((k) => (
          <label key={k} class="choice">
            <input type="radio" name="lore-known" value={k} checked={known === k} onChange={() => setKnown(k)} /> {words.known[k].label}
            <span class="small"> — {words.known[k].note}</span>
          </label>
        ))}
      </fieldset>
      <label class="choice">
        <input type="checkbox" checked={rights} onChange={(e) => setRights((e.target as HTMLInputElement).checked)} /> {words.rights}
      </label>
      <p class="small">{words.rightsNote}</p>
      {problem && <p role="alert" ref={alertBox} tabIndex={-1}>{problem}</p>}
      <p>
        <button type="submit" class="button" disabled={busy}>{busy ? words.sending : words.submit}</button>
      </p>
    </form>
  );
}

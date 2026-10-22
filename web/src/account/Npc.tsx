/** @jsxImportSource preact */
// An NPC's page (#2266): what the table already knows about one name (the same as Discord's NPC
// list shows the players), the posted recaps that mention it, a DM's own note (DM only), and the
// signed-in person's private notes. Who may see what is the API's rule: this page only shows what it
// is given. My notes belong to one person; nobody else, the DM included, can read them.
import { useEffect, useRef, useState } from "preact/hooks";

import { NOTE_LIMIT, npcPage as words } from "../content/npc-words";
import { type AccountApi, ApiError, type MyNpcNote, type NpcPage } from "./api";

/** The campaign and NPC in the address: `/campaigns/<server>.<campaign>/npcs/<entry>`. */
export function npcFromPath(path: string): { ref: string; entry: string } | null {
  const match = /^\/campaigns\/([^/]+)\/npcs\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return { ref: decodeURIComponent(match[1] ?? ""), entry: decodeURIComponent(match[2] ?? "") };
  } catch {
    return null; // a broken %-escape is just a name that isn't there
  }
}

type View = "loading" | "ok" | "none" | "outside" | "down" | "signed-out";

export default function Npc({ api, path }: { api: AccountApi; path: string }) {
  const where = npcFromPath(path);
  const [view, setView] = useState<View>("loading");
  const [page, setPage] = useState<NpcPage | null>(null);

  async function load() {
    if (!where || !api.npcPage) return setView("none");
    setView("loading");
    try {
      const read = await api.npcPage(where.ref, where.entry);
      setPage(read);
      setView("ok");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setView(
        kind === "signed-out"
          ? "signed-out"
          : kind === "not-in-server"
            ? "outside"
            : kind === "not-found"
              ? "none"
              : "down",
      );
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, path]);

  if (view === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{words.signedOut}</p>
      </div>
    );
  if (view === "outside") return <p role="status">{words.notInServer}</p>;
  if (view === "none" || !where) return <p role="status">{words.notFound}</p>;
  if (view === "down")
    return (
      <div class="stack">
        <p class="note bad" role="alert">
          {words.down}
        </p>
        <button type="button" class="button secondary" onClick={() => void load()}>
          {words.tryAgain}
        </button>
      </div>
    );
  if (view === "loading" || !page)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  const nothing =
    page.alsoCalled.length === 0 && page.groups.length === 0 && page.ties.length === 0;
  return (
    <div class="stack npc-page">
      <section class="stack" aria-labelledby="npc-name">
        <h2 id="npc-name">{page.name}</h2>
        <p class="muted">{words.kindOf(page.kind)}</p>
        {page.alsoCalled.length > 0 && (
          <p>
            <strong>{words.alsoCalled}:</strong> {page.alsoCalled.join(", ")}
          </p>
        )}
        <Listing title={words.groups} items={page.groups} more={page.groupsMore} />
        <Listing title={words.ties} items={page.ties} more={page.tiesMore} />
        {nothing && <p class="small muted">{words.nothingElse}</p>}
      </section>

      {page.recaps.length > 0 && (
        <section class="stack" aria-labelledby="npc-recaps">
          <h2 id="npc-recaps">{words.recaps}</h2>
          <ul>
            {page.recaps.map((r) => (
              <li key={r.ref}>
                <a href={`/account/recaps/${encodeURIComponent(r.ref)}`}>
                  {words.recapLink(r.title, r.day)}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {page.isDm && page.dmNote !== null && (
        <section class="stack" aria-labelledby="npc-dm">
          <h2 id="npc-dm">{words.dmNote}</h2>
          <p class="small muted">{words.dmNoteWho}</p>
          <p class="panel">{page.dmNote === "" ? "—" : page.dmNote}</p>
        </section>
      )}

      <MyNotes api={api} refId={where.ref} entry={where.entry} first={page.myNote} />
      <AllNotes api={api} refId={where.ref} />
    </div>
  );
}

function Listing({ title, items, more }: { title: string; items: string[]; more: number }) {
  if (items.length === 0) return null;
  return (
    <div>
      <strong>{title}</strong>
      <ul>
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
        {more > 0 && <li class="muted">{words.more(more)}</li>}
      </ul>
    </div>
  );
}

/** The person's own note: saved on leaving the box and with Save. */
function MyNotes({
  api,
  refId,
  entry,
  first,
}: {
  api: AccountApi;
  refId: string;
  entry: string;
  first: string;
}) {
  const [text, setText] = useState(first);
  const saved = useRef(first);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed" | "long" | "quick">(
    "idle",
  );
  const busy = useRef(false);

  async function save() {
    if (!api.saveNpcNote || busy.current || text === saved.current) return;
    if (text.length > NOTE_LIMIT) return setState("long");
    busy.current = true;
    setState("saving");
    const sent = text;
    try {
      await api.saveNpcNote(refId, entry, sent);
      saved.current = sent;
      setState("saved");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setState(kind === "bad-text" ? "long" : kind === "too-soon" ? "quick" : "failed");
    } finally {
      busy.current = false;
    }
  }

  const said =
    state === "saving"
      ? words.saving
      : state === "saved"
        ? words.saved
        : state === "long"
          ? words.tooLong
          : state === "quick"
            ? words.tooSoon
            : state === "failed"
              ? words.saveFailed
              : "";
  return (
    <section class="stack" aria-labelledby="npc-mine">
      <h2 id="npc-mine">{words.notesHeading}</h2>
      <p class="small muted">{words.notesWho}</p>
      <label class="field">
        {words.notesLabel}
        <textarea
          rows={6}
          value={text}
          aria-describedby="npc-count"
          onInput={(e) => {
            setText((e.target as HTMLTextAreaElement).value);
            setState("idle");
          }}
          onBlur={() => void save()}
        />
      </label>
      <p id="npc-count" class={text.length > NOTE_LIMIT ? "small warn" : "small muted"}>
        {words.counter(text.length)}
      </p>
      <p>
        <button type="button" class="button" disabled={state === "saving"} onClick={() => void save()}>
          {words.save}
        </button>{" "}
        <span role="status" class={state === "failed" || state === "long" || state === "quick" ? "warn" : "muted"}>
          {said}
        </span>
      </p>
    </section>
  );
}

/** A plain text file of every note, oldest line first as the API sent them. */
function notesFile(notes: MyNpcNote[]): string {
  return (
    [words.fileHeading, ...notes.map((n) => `\n${n.name}\n${n.body}`)].join("\n") + "\n"
  );
}

/** Download or delete all of my notes in this campaign, with those on names no longer listed. */
function AllNotes({ api, refId }: { api: AccountApi; refId: string }) {
  const [notes, setNotes] = useState<MyNpcNote[] | null>(null);
  const [asking, setAsking] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const busy = useRef(false);

  async function refresh() {
    if (!api.myNpcNotes) return;
    try {
      setNotes(await api.myNpcNotes(refId));
    } catch {
      setNotes(null);
    }
  }
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refId]);

  if (!api.myNpcNotes || !api.deleteMyNpcNotes) return null;

  async function download() {
    if (busy.current) return;
    busy.current = true;
    setSaid({ ok: true, text: words.downloading });
    try {
      const all = await api.myNpcNotes!(refId);
      const href = URL.createObjectURL(new Blob([notesFile(all)], { type: "text/plain" }));
      const link = document.createElement("a");
      link.href = href;
      link.download = words.fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setSaid(null);
    } catch {
      setSaid({ ok: false, text: words.downloadFailed });
    } finally {
      busy.current = false;
    }
  }

  async function remove() {
    if (busy.current) return;
    busy.current = true;
    try {
      const n = await api.deleteMyNpcNotes!(refId);
      setAsking(false);
      setNotes([]);
      setSaid({ ok: true, text: words.deleted(n) });
    } catch {
      setSaid({ ok: false, text: words.deleteFailed });
    } finally {
      busy.current = false;
    }
  }

  const gone = (notes ?? []).filter((n) => !n.listed);
  return (
    <section class="stack" aria-labelledby="npc-all">
      <h2 id="npc-all">{words.allHeading}</h2>
      {notes !== null && notes.length === 0 && <p class="small muted">{words.noneYet}</p>}
      {gone.length > 0 && (
        <div>
          <strong>{words.unlistedHeading}</strong>
          <ul>
            {gone.map((n) => (
              <li key={n.id}>{n.name}</li>
            ))}
          </ul>
          <p class="small muted">{words.unlistedNote}</p>
        </div>
      )}
      <div class="row">
        <button type="button" class="button secondary" onClick={() => void download()}>
          {words.download}
        </button>
        {!asking && (
          <button
            type="button"
            class="button secondary"
            disabled={notes !== null && notes.length === 0}
            onClick={() => setAsking(true)}
          >
            {words.deleteAsk}
          </button>
        )}
      </div>
      {asking && (
        <div class="panel" role="group" aria-label={words.deleteAsk}>
          <p>{words.deleteSure((notes ?? []).length)}</p>
          <button type="button" class="button" onClick={() => void remove()}>
            {words.deleteYes}
          </button>{" "}
          <button type="button" class="button secondary" onClick={() => setAsking(false)}>
            {words.deleteNo}
          </button>
        </div>
      )}
      <p role="status" class={said && !said.ok ? "warn" : "muted"}>
        {said?.text ?? ""}
      </p>
    </section>
  );
}

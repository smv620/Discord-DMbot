/** @jsxImportSource preact */
// The DM's recap pages (#1386): the list, and the page to read, change and decide one recap.
// The API shows a recap only to a DM of its campaign, so a player gets "not found" and nothing
// is guarded here beyond that; nothing is posted from this page, the bot posts after Post.
import { useEffect, useRef, useState } from "preact/hooks";

import {
  againLabel,
  errors,
  sessionDay,
  statusLine,
  text,
} from "../content/recaps";
import { RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type Recap, type RecapAction } from "./api";

const POLL_MS = 5000;

type Load =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; recaps: Recap[] };

export function messageFor(error: unknown): string {
  const kind = error instanceof ApiError ? error.kind : "server";
  return errors[kind] ?? errors.other;
}

/** The recap's id in the address, `/account/recaps/<id>`, or null on the list page. */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/recaps\/([^/]+?)\/?$/.exec(path);
  return match ? decodeURIComponent(match[1] ?? "") : null;
}

export default function Recaps({
  api,
  path,
}: {
  api: AccountApi;
  path: string;
}) {
  const ref = refFromPath(path);
  return (
    <div class="stack">
      <p>
        <a href="/account">{text.account}</a>
      </p>
      {ref ? <Review api={api} recapRef={ref} /> : <List api={api} />}
    </div>
  );
}

function SignedOut({ api }: { api: AccountApi }) {
  return (
    <div class="panel" role="status">
      <p>{text.signedOut}</p>
      <a class="button" href={api.signInUrl()}>
        {text.signIn}
      </a>
    </div>
  );
}

function List({ api }: { api: AccountApi }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    if (!api.recaps) {
      setLoad({ kind: "error", message: errors.other });
      return;
    }
    api.recaps().then(
      (recaps) => live && setLoad({ kind: "ready", recaps }),
      (error: unknown) => {
        if (!live) return;
        if (error instanceof ApiError && error.kind === "signed-out")
          setLoad({ kind: "signed-out" });
        else setLoad({ kind: "error", message: messageFor(error) });
      },
    );
    return () => {
      live = false;
    };
  }, [api]);

  if (load.kind === "loading")
    return (
      <p class="muted" role="status">
        {text.loading}
      </p>
    );
  if (load.kind === "signed-out") return <SignedOut api={api} />;
  if (load.kind === "error")
    return (
      <p class="warn" role="alert">
        {load.message}
      </p>
    );
  return (
    <>
      {(RECAPS_LIVE || load.recaps.length > 0) && (
        <p class="lead">{text.lead}</p>
      )}
      {load.recaps.length === 0 ? (
        <p class="panel" role="status">
          {RECAPS_LIVE ? text.empty : text.emptyOff}
        </p>
      ) : (
        <ul class="campaigns">
          {load.recaps.map((r) => (
            <li key={r.ref}>
              <span class="campaign-name">{r.title || text.untitled}</span>
              <span class="small">
                {[r.campaign, r.serverName, sessionDay(r.sessionAt)]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              <span class="tags">
                <span class="tag">{statusLine(r)}</span>
              </span>
              <a href={`/account/recaps/${encodeURIComponent(r.ref)}`}>
                {text.open}
              </a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Review({ api, recapRef }: { api: AccountApi; recapRef: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [recap, setRecap] = useState<Recap | null>(null);
  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState("");
  const [bullets, setBullets] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(
    null,
  );
  // A second tap before the page redraws must not send a second request.
  const inFlight = useRef(false);

  useEffect(() => {
    let live = true;
    const read = api.recap;
    if (!read) {
      setLoad({ kind: "error", message: errors.other });
      return;
    }
    read(recapRef).then(
      (r) => {
        if (!live) return;
        setRecap(r);
        setLoad({ kind: "ready", recaps: [r] });
      },
      (error: unknown) => {
        if (!live) return;
        if (error instanceof ApiError && error.kind === "signed-out")
          setLoad({ kind: "signed-out" });
        else setLoad({ kind: "error", message: messageFor(error) });
      },
    );
    return () => {
      live = false;
    };
  }, [api, recapRef]);

  // While DMbot is writing it or posting it, look again every few seconds.
  const waiting =
    recap !== null && (recap.working || recap.status === "posting");
  useEffect(() => {
    if (!waiting || !api.recap) return;
    const timer = setInterval(() => {
      if (inFlight.current) return;
      api.recap?.(recapRef).then(setRecap, () => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [api, recapRef, waiting]);

  if (load.kind === "loading")
    return (
      <p class="muted" role="status">
        {text.loading}
      </p>
    );
  if (load.kind === "signed-out") return <SignedOut api={api} />;
  if (load.kind === "error" || !recap) {
    return (
      <>
        <p class="warn" role="alert">
          {load.kind === "error" ? load.message : errors.other}
        </p>
        <p>
          <a href="/account/recaps">{text.back}</a>
        </p>
      </>
    );
  }

  // After a refusal the page may be out of date (posted from another tab, say): read it again.
  const refresh = async (error: unknown) => {
    if (
      !(error instanceof ApiError) ||
      !["not-allowed", "not-editable"].includes(error.kind)
    )
      return;
    try {
      const fresh = await api.recap?.(recapRef);
      if (fresh) {
        setRecap(fresh);
        setEditing(false);
      }
    } catch {
      // The message above already says to reload.
    }
  };

  const startEdit = () => {
    setSummary(recap.summary);
    setBullets(recap.housekeeping.join("\n"));
    setNote(null);
    setEditing(true);
  };

  const save = async (event: Event) => {
    event.preventDefault();
    if (!api.saveRecap || inFlight.current) return;
    inFlight.current = true;
    setBusy("save");
    setNote(null);
    try {
      const lines = bullets
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      setRecap(await api.saveRecap(recap.ref, summary, lines));
      setEditing(false);
      setNote({ ok: true, message: text.saved });
    } catch (error) {
      setNote({ ok: false, message: messageFor(error) });
      await refresh(error);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const decide = async (action: RecapAction) => {
    if (!api.decideRecap || inFlight.current) return;
    inFlight.current = true;
    setBusy(action);
    setNote(null);
    try {
      const next = await api.decideRecap(recap.ref, action);
      setRecap(next);
      setEditing(false);
      // Post and Don't post are said by the status lines above; only Try again needs a note.
      if (action === "again") setNote({ ok: true, message: text.tryingAgain });
    } catch (error) {
      setNote({ ok: false, message: messageFor(error) });
      await refresh(error);
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const heading = recap.title || text.untitled;
  const decided = recap.status !== "draft";
  return (
    <>
      <div class="hello">
        <h2>{heading}</h2>
        <span class="tag">{statusLine(recap)}</span>
      </div>
      <p class="small">
        {[recap.campaign, recap.serverName, sessionDay(recap.sessionAt)]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {!decided && !recap.working && recap.state !== "failed" && (
        <>
          <p class="lead">{text.onlyYou}</p>
          <p>{text.beforeChoosing}</p>
        </>
      )}
      {recap.status === "posted" && (
        <p class="ok" role="status">
          {text.posted}
        </p>
      )}
      {recap.status === "posting" && (
        <p class="ok" role="status">
          {text.posting}
        </p>
      )}
      {recap.status === "dropped" && <p role="status">{text.dropped}</p>}
      {note && (
        <p class={note.ok ? "ok" : "warn"} role={note.ok ? "status" : "alert"}>
          {note.message}
        </p>
      )}
      {recap.working && (
        <p class="panel" role="status">
          {text.working}
        </p>
      )}
      {recap.state === "failed" && !decided && (
        <p class="warn" role="alert">
          {text.failed}
        </p>
      )}

      {editing ? (
        <form class="panel" onSubmit={save}>
          <label for="recap-text">{text.editLabel}</label>
          <textarea
            id="recap-text"
            rows={14}
            value={summary}
            onInput={(e) => setSummary((e.target as HTMLTextAreaElement).value)}
          />
          <label for="recap-housekeeping">{text.housekeepingLabel}</label>
          <textarea
            id="recap-housekeeping"
            rows={5}
            value={bullets}
            onInput={(e) => setBullets((e.target as HTMLTextAreaElement).value)}
          />
          <div class="row">
            <button type="submit" class="button" disabled={busy !== null}>
              {busy === "save" ? text.saving : text.save}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => setEditing(false)}
            >
              {text.cancel}
            </button>
          </div>
        </form>
      ) : (
        recap.text && (
          <>
            <section class="panel">
              <h3>{text.textHeading}</h3>
              {recap.text.split(/\n{2,}/).map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </section>
            <section class="panel">
              <h3>{text.housekeepingHeading}</h3>
              {recap.housekeeping.length === 0 ? (
                <p>{text.housekeepingNone}</p>
              ) : (
                <ul>
                  {recap.housekeeping.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )
      )}

      {!editing &&
        (recap.canEdit ||
          recap.canPost ||
          recap.canTryAgain ||
          recap.canDrop ||
          !decided) && (
          <div class="row">
            {recap.canEdit && (
              <button
                type="button"
                class="button secondary"
                onClick={startEdit}
                disabled={busy !== null}
              >
                {text.edit}
              </button>
            )}
            {recap.canPost && (
              <button
                type="button"
                class="button"
                onClick={() => decide("post")}
                disabled={busy !== null}
              >
                {busy === "post" ? text.busy : text.post}
              </button>
            )}
            {!decided && (
              <button
                type="button"
                class="button secondary"
                onClick={() => decide("again")}
                disabled={busy !== null || !recap.canTryAgain}
              >
                {busy === "again" ? text.busy : againLabel(recap.triesLeft)}
              </button>
            )}
            {recap.canDrop && (
              <button
                type="button"
                class="button secondary"
                onClick={() => decide("drop")}
                disabled={busy !== null}
              >
                {busy === "drop" ? text.busy : text.drop}
              </button>
            )}
          </div>
        )}
      {!decided && !recap.canTryAgain && recap.triesLeft === 0 && (
        <p class="small">{text.noTriesLeft}</p>
      )}
      <p>
        <a href="/account/recaps">{text.back}</a>
      </p>
    </>
  );
}

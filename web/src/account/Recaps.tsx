/** @jsxImportSource preact */
// The DM's recap pages (#1386): the list, and the page to read, change and decide one recap.
// The API shows a recap only to a DM of its campaign, so a player gets "not found" and nothing
// is guarded here beyond that; nothing is posted from this page, the bot posts after Post.
import { useEffect, useRef, useState } from "preact/hooks";

import {
  againLabel,
  allowanceLines,
  errors,
  sessionDay,
  statusLine,
  text,
} from "../content/recaps";
import { accountWords as audioWords, price as audioPrice } from "../content/recap-audio";
import { panelWords } from "../content/recap-panel";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE } from "../content/recap-words";
import {
  ApiError,
  type AccountApi,
  type Recap,
  type RecapAction,
  type RecapMonth,
  type RecapAudioLinks,
} from "./api";
import CuedText, { hasCues } from "./CuedText";
import RecapChoicesPart from "./RecapChoices";
import { keepFocus } from "./focus";
import RecapNames from "./RecapNames";
import RecapPlayer from "./RecapPlayer";

const POLL_MS = 5000;


type Load =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; recaps: Recap[]; thisMonth: RecapMonth[] };

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
    // The list with the month's allowance when the API has it (#1444), else the plain list.
    const read = api.recapList
      ? api.recapList()
      : api.recaps().then((recaps) => ({ recaps, thisMonth: [] as RecapMonth[] }));
    read.then(
      ({ recaps, thisMonth }) => live && setLoad({ kind: "ready", recaps, thisMonth }),
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
      {/* Behind the same switch as the list's other promises (#1423): nothing while recaps are off. */}
      {RECAPS_LIVE &&
        allowanceLines(load.thisMonth, RECAP_AUDIO_LIVE, new Date()).map((line) => (
          <p key={line} class="small muted">
            {line}
          </p>
        ))}
    </>
  );
}

/** Where narrated recaps are not part of the plan: one line and a button to add them (#1390).
 * Nothing at all while the audio is off, or for a plan that has them. */
function AudioOffer({ api }: { api: AccountApi }) {
  const [offer, setOffer] = useState<"none" | "buy">("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!RECAP_AUDIO_LIVE) return;
    let live = true;
    api.me().then(
      (me) => {
        const audio = me?.recapAudio;
        if (!live || !audio || audio.included || audio.addOn) return;
        if (audio.canBuy && api.recapAudioUrl) setOffer("buy");
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  if (offer === "none") return null;
  const buy = async (): Promise<void> => {
    if (!api.recapAudioUrl || busy) return;
    setBusy(true);
    setError(null);
    try {
      window.location.assign(await api.recapAudioUrl());
    } catch (e) {
      const kind = e instanceof ApiError ? e.kind : "server";
      setError(
        kind === "already-included" || kind === "already-subscribed"
          ? audioWords.errors[kind]
          : errors.other,
      );
      setBusy(false);
    }
  };
  return (
    <div class="panel">
      <p class="small">{audioWords.notIncluded}</p>
      <button type="button" class="button secondary" disabled={busy} onClick={() => void buy()}>
        {busy ? text.busy : audioWords.buy}
      </button>
      <p class="muted small">{audioWords.buyNote(audioPrice())}</p>
      {error && (
        <p class="warn" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** The narration and music (#1418, #2017): one player for the voice and music together plus the mixed
 * file while the audio matches the text, otherwise a line saying where it is, and a button to make it
 * again. Only while the audio is on. */
function AudioPanel({
  api,
  recap,
  onRecap,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
}) {
  const audio = recap.audio;
  const [links, setLinks] = useState<RecapAudioLinks>({});
  const [lost, setLost] = useState(false);
  const [busy, setBusy] = useState(false);
  // A second tap before the page redraws must not send a second request.
  const asking = useRef(false);
  // One fresh read of the links when a player fails; more would loop on a file that is gone.
  const retried = useRef(false);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(
    null,
  );
  const playable = audio?.state === "ready" && audio.current;

  // The links last 15 minutes; one fresh read when a player cannot load its file.
  const load = async (): Promise<void> => {
    try {
      setLinks((await api.recapAudioLinks?.(recap.ref)) ?? {});
      setLost(false);
    } catch {
      setLost(true);
    }
  };
  const failed = (): void => {
    if (retried.current) {
      setLost(true);
      return;
    }
    retried.current = true;
    void load();
  };
  useEffect(() => {
    retried.current = false;
    if (!RECAP_AUDIO_LIVE || !playable) {
      setLinks({});
      return;
    }
    let live = true;
    api.recapAudioLinks?.(recap.ref).then(
      (found) => {
        if (live) {
          setLinks(found);
          setLost(false);
        }
      },
      () => live && setLost(true),
    );
    return () => {
      live = false;
    };
  }, [api, recap.ref, playable]);

  if (!RECAP_AUDIO_LIVE || !audio) return null;

  const ask = async (): Promise<void> => {
    if (!api.askRecapAudioAgain || asking.current) return;
    asking.current = true;
    setBusy(true);
    setNote(null);
    try {
      onRecap(await api.askRecapAudioAgain(recap.ref));
      setNote({ ok: true, message: panelWords.asked });
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setNote({
        ok: false,
        message:
          kind === "too-soon"
            ? panelWords.tooSoon
            : (errors[kind] ?? errors.other),
      });
    } finally {
      asking.current = false;
      setBusy(false);
      keepFocus(() => document.getElementById("recap-audio-again"));
    }
  };

  const line =
    audio.state === "queued"
      ? panelWords.queued
      : audio.state === "failed"
        ? audio.error || panelWords.failed
        : audio.state === "ready" && !audio.current
          ? panelWords.stale
          : audio.state === "off"
            ? panelWords.off
            : null;
  return (
    <section class="panel" aria-labelledby="recap-audio-panel">
      <h3 id="recap-audio-panel">{panelWords.heading}</h3>
      {playable && (links.voice || links.music) && (
        <RecapPlayer voice={links.voice} music={links.music} onFail={failed} />
      )}
      {playable && links.mix && (
        <div>
          <p class="small">{panelWords.mix}</p>
          <audio
            controls
            preload="none"
            src={links.mix}
            aria-label={panelWords.mix}
            onError={failed}
          />
        </div>
      )}
      {playable && lost && (
        <p class="warn" role="alert">
          {panelWords.expired}
        </p>
      )}
      {line && <p role="status">{line}</p>}
      {audio.canAskAgain && (
        <button
          type="button"
          class="button secondary"
          id="recap-audio-again"
          disabled={busy}
          onClick={() => void ask()}
        >
          {busy ? text.busy : panelWords.again}
        </button>
      )}
      {note && (
        <p class={note.ok ? "ok" : "warn"} role={note.ok ? "status" : "alert"}>
          {note.message}
        </p>
      )}
    </section>
  );
}

function Review({ api, recapRef }: { api: AccountApi; recapRef: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [recap, setRecap] = useState<Recap | null>(null);
  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState("");
  const [bullets, setBullets] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Where focus goes when the button that was pressed is disabled or gone (#1466).
  const headingRef = useRef<HTMLHeadingElement>(null);
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
        setLoad({ kind: "ready", recaps: [r], thisMonth: [] });
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
    recap !== null &&
    (recap.working || recap.status === "posting" || recap.audio?.state === "queued");
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
      keepFocus(() => headingRef.current);
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
      keepFocus(() => headingRef.current);
    }
  };

  const heading = recap.title || text.untitled;
  const decided = recap.status !== "draft";
  return (
    <>
      <div class="hello">
        <h2 tabIndex={-1} ref={headingRef}>
          {heading}
        </h2>
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
            {RECAP_AUDIO_LIVE && recap.audio && hasCues(recap.summary) && (
              <section class="panel" aria-labelledby="recap-cues">
                <h3 id="recap-cues">{panelWords.cuesHeading}</h3>
                <p class="muted small">{panelWords.cuesNote}</p>
                <CuedText text={recap.summary} />
              </section>
            )}
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

      {!editing && <AudioPanel api={api} recap={recap} onRecap={setRecap} />}
      {!editing && <AudioOffer api={api} />}
      {!editing && <RecapChoicesPart api={api} recapRef={recap.ref} />}
      {!editing && <RecapNames api={api} recapRef={recap.ref} />}
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

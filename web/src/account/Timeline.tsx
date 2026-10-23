/** @jsxImportSource preact */
// The Story timeline page (#2462): a campaign's sessions in order, each with its recap and the names
// shown to the players at the table. Read-only. The API decides what each person sees: a game master
// sees every approved recap and its page, a player only the posted ones, as plain titles.
import { useEffect, useState } from "preact/hooks";

import { playedDay, sessionLength } from "../content/recaps";
import { problem, text } from "../content/timeline";
import { ApiError, type AccountApi, type Timeline, type TimelineSession } from "./api";
import CampaignNav from "./CampaignNav";

/** The campaign in the address: `/account/timeline/<server>.<campaign>`, or null. */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/timeline\/([^/]+?)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null;
  }
}

type Load =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; value: Timeline };

function Entry({ s }: { s: TimelineSession }) {
  const length = s.endedAt === null ? "" : sessionLength(s.endedAt - s.startedAt);
  const id = `session-${s.number}`;
  return (
    <li class="timeline-session" aria-labelledby={id}>
      <h2 id={id}>{text.session(s.number)}</h2>
      <p class="small muted">
        {[playedDay(s.startedAt), s.endedAt === null ? text.running : length].filter((p) => p !== "").join(" · ")}
      </p>
      <p>
        <strong>{text.recapLabel}: </strong>
        {s.recap === null ? (
          text.noRecap
        ) : s.recap.ref ? (
          <a href={`/account/recaps/${encodeURIComponent(s.recap.ref)}`} aria-label={text.openRecap(s.recap.title)}>
            {s.recap.title}
          </a>
        ) : (
          s.recap.title
        )}
        {s.recap !== null && <span class="small muted"> ({s.recap.posted ? text.recapPosted : text.recapApproved})</span>}
      </p>
      <p>
        <strong>{text.revealedLabel}: </strong>
        {s.revealed.length === 0 ? text.revealedNone : s.revealed.join(", ")}
      </p>
    </li>
  );
}

export default function TimelinePage({ api, path }: { api: AccountApi; path: string }) {
  const ref = refFromPath(path);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    if (!ref || !api.timeline) {
      setLoad({ kind: "error", message: text.notFound });
      return;
    }
    setLoad({ kind: "loading" });
    api.timeline(ref).then(
      (value) => live && setLoad({ kind: "ready", value }),
      (error: unknown) => {
        if (!live) return;
        const kind = error instanceof ApiError ? error.kind : "server";
        setLoad(kind === "signed-out" ? { kind: "signed-out" } : { kind: "error", message: problem(kind) });
      },
    );
    return () => {
      live = false;
    };
  }, [api, ref]);

  return (
    <div class="stack timeline-page">
      {ref && (
        <CampaignNav api={api} campaignRef={ref} page="timeline" name={load.kind === "ready" ? load.value.name : null} />
      )}
      {load.kind === "loading" && (
        <p class="muted" role="status">
          {text.loading}
        </p>
      )}
      {load.kind === "signed-out" && (
        <div class="panel" role="status">
          <p>{text.signedOut}</p>
        </div>
      )}
      {load.kind === "error" && (
        <p class="warn" role="alert">
          {load.message}
        </p>
      )}
      {load.kind === "ready" &&
        (load.value.sessions.length === 0 ? (
          <p class="panel" role="status">
            {text.none}
          </p>
        ) : (
          <>
            <p class="lead">{text.lead}</p>
            <ol class="timeline">
              {load.value.sessions.map((s) => (
                <Entry key={s.number} s={s} />
              ))}
            </ol>
          </>
        ))}
    </div>
  );
}

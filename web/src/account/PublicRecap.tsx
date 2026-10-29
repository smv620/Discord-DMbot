/** @jsxImportSource preact */
// A shared recap, read by anyone who has its link (#2119; core #2118): the title, the table version
// of the text and the player for the voice and the music, and nothing else (no editing, no names list,
// no footer). Every dead link gets the same plain message, because the API answers the same for a wrong,
// never-made, turned-off or no-longer-passing link. Shown only while RECAP_SHARE_LIVE is true.
import { useEffect, useState } from "preact/hooks";

import { publicWords as words } from "../content/recap-share";
import { RECAP_SHARE_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { sessionDay, sessionLine } from "../content/recaps";
import { ApiError, type AccountApi, type PublicRecap } from "./api";
import RecapPlayer from "./RecapPlayer";

/** The link's secret, read from `/r/<token>`. */
export function tokenFromPath(path: string): string | null {
  const match = /^\/r\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null; // a mangled address ("/r/%E0") is a dead link, not a crash
  }
}

type Load =
  | { kind: "loading" }
  | { kind: "dead" }
  | { kind: "busy" }
  | { kind: "error" }
  | { kind: "ready"; recap: PublicRecap };

export default function PublicRecapPage({
  api,
  path,
}: {
  api: AccountApi;
  path: string;
}) {
  const token = tokenFromPath(path);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [tries, setTry] = useState(0);
  const [soundTrouble, setSoundTrouble] = useState(false);

  useEffect(() => {
    // Not live yet, or no secret in the address: nothing to ask, the same message as any dead link.
    if (!(RECAPS_LIVE && RECAP_SHARE_LIVE) || !token || !api.publicRecap) {
      setLoad({ kind: "dead" });
      return;
    }
    let live = true;
    api.publicRecap(token).then(
      (recap) => live && setLoad({ kind: "ready", recap }),
      (error: unknown) => {
        if (!live) return;
        const kind = error instanceof ApiError ? error.kind : "server";
        setLoad({
          kind:
            kind === "slow-down"
              ? "busy"
              : kind === "network" || kind === "server"
                ? "error"
                : "dead",
        });
      },
    );
    return () => {
      live = false;
    };
  }, [api, token, tries]);

  if (load.kind === "loading")
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (load.kind === "dead" || load.kind === "error" || load.kind === "busy")
    return (
      <div class="public-recap">
        <p role="status">
          {load.kind === "busy"
            ? words.busy
            : load.kind === "error"
              ? words.trouble
              : words.dead}
        </p>
        {/* A bad connection is not a dead link: the reader can try again without bothering the DM. */}
        {load.kind === "error" && (
          <button
            type="button"
            class="button"
            onClick={() => setTry((n) => n + 1)}
          >
            {words.tryAgain}
          </button>
        )}
      </div>
    );
  const { recap } = load;
  const paragraphs = recap.text.split(/\n{2,}/).filter((p) => p.trim() !== "");
  return (
    <article class="public-recap">
      <h2>{recap.title}</h2>
      {sessionLine(recap) !== "" && <p class="small">{sessionLine(recap)}</p>}
      <p class="note">
        {recap.postedAt === null ? words.approved : words.posted(sessionDay(recap.postedAt))}
      </p>
      {(recap.audio.voice || recap.audio.music) && (
        <div class="card">
          <RecapPlayer
            voice={recap.audio.voice}
            music={recap.audio.music}
            onFail={() => setSoundTrouble(true)}
          />
          {soundTrouble && <p class="note">{words.audioTrouble}</p>}
        </div>
      )}
      <div class="card">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
    </article>
  );
}

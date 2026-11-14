/** @jsxImportSource preact */
// "People you play with" (#4226): the signed-in person's two lists. As a DM, the players DMbot remembers for them, each with Forget this
// player; as a player, the DMs who remember them, each with Clear, and Clear all. Both are the person's own: a person who is no DM sees
// an empty first list and one who is no player an empty second, and the page says so in plain words. Names and dates only.
import { useEffect, useState } from "preact/hooks";

import { REMEMBERED_PLAYERS_LIVE } from "../content/remembered-players-switch";
import { rememberedWords as words } from "../content/remembered-players-words";
import { PlayersError, type PlayersApi, type RememberedBy, type RememberedPlayer } from "./players-api";

type Lists = { mine: RememberedPlayer[]; by: RememberedBy[] };

export default function RememberedPlayers({ api }: { api: PlayersApi }) {
  const [lists, setLists] = useState<Lists | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);

  async function load(): Promise<void> {
    setProblem(null);
    try {
      const [mine, by] = await Promise.all([api.mine(), api.by()]);
      setLists({ mine, by });
    } catch (e) {
      setProblem(e instanceof PlayersError && e.kind === "signed_out" ? words.signedOut : words.failed);
    }
  }
  useEffect(() => {
    if (REMEMBERED_PLAYERS_LIVE) void load();
  }, [api]);

  async function act(run: () => Promise<void>, ok: string, fail: string, gone?: string): Promise<void> {
    setBusy(true);
    setSaid("");
    try {
      await run();
      setSaid(ok);
    } catch (e) {
      setSaid(e instanceof PlayersError && e.kind === "gone" && gone ? gone : fail);
    }
    setBusy(false);
    // A reload that fails keeps the lists already on the page, so the line above is not lost with them.
    try {
      const [mine, by] = await Promise.all([api.mine(), api.by()]);
      setLists({ mine, by });
    } catch {
      setSaid((was) => `${was} ${words.failed}`.trim());
    }
  }

  if (!REMEMBERED_PLAYERS_LIVE) return <p class="muted">{words.notOpen}</p>;
  if (problem)
    return (
      <div role="alert">
        <p>{problem}</p>
        <button type="button" class="button secondary" onClick={() => void load()}>
          {words.tryAgain}
        </button>
      </div>
    );
  if (!lists)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  return (
    <div class="stack">
      <p role="status" aria-live="polite">
        {said}
      </p>
      <section aria-labelledby="rp-mine">
        <h2 id="rp-mine">{words.mine.heading}</h2>
        <p>{words.mine.intro}</p>
        {lists.mine.length === 0 ? (
          <p class="muted">{words.mine.none}</p>
        ) : (
          <ul class="plain">
            {lists.mine.map((p) => {
              const name = p.name || words.mine.unknownName;
              return (
                <li key={p.player}>
                  <strong>{name}</strong> <span class="muted">{words.mine.row(p.campaigns, p.since)}</span>{" "}
                  <button type="button" class="button secondary" disabled={busy} aria-label={words.mine.forget(name)} onClick={() => void act(() => api.forget(p.player), words.mine.forgotten(name), words.actionFailed, words.mine.already)}>
                    {words.mine.forgetButton}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <p class="small muted">{words.mine.note}</p>
      </section>
      <section aria-labelledby="rp-by">
        <h2 id="rp-by">{words.by.heading}</h2>
        {lists.by.length > 0 && <p>{words.by.intro}</p>}
        {lists.by.length === 0 ? (
          <p class="muted">{words.by.none}</p>
        ) : (
          <>
            <ul class="plain">
              {lists.by.map((d) => {
                const name = d.name || words.by.unknownName;
                return (
                  <li key={d.dm}>
                    <strong>{name}</strong> <span class="muted">{words.by.row(d.campaigns, d.since)}</span>{" "}
                    <button type="button" class="button secondary" disabled={busy} aria-label={words.by.clear(name)} onClick={() => void act(() => api.clear(d.dm), words.by.cleared(name), words.actionFailedBy)}>
                      {words.by.clearButton}
                    </button>
                  </li>
                );
              })}
            </ul>
            {lists.by.length > 1 && (
              <button type="button" class="button secondary" disabled={busy} onClick={() => void act(() => api.clear("all"), words.by.clearedAll, words.actionFailedBy)}>
                {words.by.clearAll}
              </button>
            )}
          </>
        )}
        {lists.by.length > 0 && <p class="small muted">{words.by.note}</p>}
      </section>
    </div>
  );
}

/** @jsxImportSource preact */
// The game masters section of a campaign's Settings tab (#2768; core web/game_masters.py): who the campaign's game
// masters are, one to remove and a search to add another. Adding and removing go to the bot through the settings queue
// and wait for its answer, which applies the same rules as Discord's 👑 Game masters (who may, the limit, the plan
// holder who stays, the last one who stays). The wait shows what DMbot is doing, how long it has run and when it last
// moved (#2564). Only people who agreed to be recorded are offered. Loaded only when the game master opens the
// Settings tab (LazyPanel), and shown only while CO_DMS_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { gameMasterWords as w } from "../content/game-masters-words";
import { stageWords } from "../content/work-progress";
import { ApiError, type AccountApi, type GameMaster, type GameMasterCandidate, type GameMasters } from "./api";
import { GIVE_UP_MS, LATE_POLL_MS, OFFLINE_AFTER_MS, POLL_MS } from "./settings-timing";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

type Phase =
  | { kind: "asking" } // the remove button was pressed: one confirmation
  | { kind: "saving"; seen: "waiting" | "working" }
  | { kind: "gaveup" }
  | { kind: "saved"; note: string }
  | { kind: "error"; message: string };

const SEARCH_DELAY_MS = 250;
const problem = (kind: string): string => (Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
const ENDS = ["not-allowed", "not-found", "signed-out"]; // looking again will not mend these
/** Discord's own words for a refusal come back as bold text; the page shows them plain. */
const plain = (note: string): string => note.replaceAll("**", "");
/** What the bot says when it cannot do it: Discord's own words for a refusal, else the page's. */
const botWords = (error: string, note: string): string =>
  error === "refused" && note
    ? plain(note)
    : error === "not_allowed"
      ? problem("not-allowed")
      : error === "not_found"
        ? problem("not-found")
        : error === "bad_value"
          ? problem("bad-value")
          : problem("server");

function Waiting({ phase }: { phase: Phase | undefined }) {
  const saving = phase?.kind === "saving";
  const seen = useObservedWait(saving, saving ? phase.seen : "");
  return (
    <>
      {phase?.kind === "saving" && seen.startedAt !== null && (
        <WorkProgress
          stage={stageWords(w.stage, phase.seen)}
          startedAt={seen.startedAt}
          updatedAt={seen.movedAt}
          state={seen.quiet ? "stalled" : "running"}
          whatToDo={w.quiet}
        />
      )}
      {phase?.kind === "gaveup" && <WorkProgress stage={stageWords(w.stage, "waiting")} state="stalled" frozen whatToDo={w.gaveUp} />}
      <p class={phase?.kind === "saved" ? "" : "small muted"} role="status">
        {phase?.kind === "saved" ? phase.note : ""}
      </p>
      <p class="warn small" role="alert">
        {phase?.kind === "error" ? phase.message : ""}
      </p>
    </>
  );
}

export default function GameMastersSection({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<GameMasters | "loading" | "failed" | "left">("loading");
  const [people, setPeople] = useState<GameMasterCandidate[] | "loading" | "failed">("loading");
  const [query, setQuery] = useState("");
  const [phases, setPhases] = useState<Record<string, Phase>>({});
  const live = useRef(true);
  const looked = useRef(0); // the newest search; an older answer arriving late is dropped
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const readList = async () => {
    if (!api.campaignGameMasters) return setList("failed");
    try {
      const found = await api.campaignGameMasters(campaignRef);
      if (live.current) setList(found);
    } catch (error) {
      // A game master who removed themselves may not read the list any more: say so, not "failed".
      if (live.current && error instanceof ApiError && error.kind === "not-allowed") return setList("left");
      if (live.current) setList((before) => (typeof before === "object" ? before : "failed"));
    }
  };
  const search = async (text: string) => {
    if (!api.campaignGameMasterCandidates) return setPeople("failed");
    const mine = (looked.current += 1);
    try {
      const found = await api.campaignGameMasterCandidates(campaignRef, text);
      if (live.current && mine === looked.current) setPeople(found);
    } catch {
      if (live.current && mine === looked.current) setPeople("failed");
    }
  };
  useEffect(() => {
    void readList();
  }, [api, campaignRef]);
  useEffect(() => {
    const timer = setTimeout(() => void search(query), query === "" ? 0 : SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [api, campaignRef, query]);

  const phase = (id: string, next: Phase | null) =>
    live.current &&
    setPhases((before) => {
      const { [id]: _gone, ...rest } = before;
      return next ? { ...rest, [id]: next } : rest;
    });

  /** The bot has not answered in the hour the queue keeps a choice: it may still be applied later, so the list is read
   * again (it shows where things stand) and the page says so, not that nothing was saved. */
  async function gaveUp(id: string) {
    phase(id, { kind: "gaveup" });
    await readList();
  }

  async function send(person: { userId: string; name: string }, add: boolean) {
    const id = person.userId;
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return phase(id, { kind: "error", message: problem("server") });
    phase(id, { kind: "saving", seen: "waiting" });
    let answer: string;
    try {
      answer = await api.setCampaignSetting(campaignRef, { setting: "co_dm", value: id, on: add });
    } catch (error) {
      return phase(id, { kind: "error", message: problem(error instanceof ApiError ? error.kind : "server") });
    }
    const started = Date.now();
    for (let i = 0; live.current; i += 1) {
      const behind = Date.now() - started > OFFLINE_AFTER_MS;
      await new Promise((r) => setTimeout(r, behind ? LATE_POLL_MS : POLL_MS[Math.min(i, POLL_MS.length - 1)]));
      if (!live.current) return;
      const final = Date.now() - started > GIVE_UP_MS;
      let got;
      try {
        got = await api.campaignSettingAnswer(campaignRef, answer);
      } catch (error) {
        if (error instanceof ApiError && ENDS.includes(error.kind)) return phase(id, { kind: "error", message: problem(error.kind) });
        if (final) return gaveUp(id);
        continue; // a look that fails is tried again until the hour is up
      }
      if (got.state === "done") {
        await readList();
        void search(query);
        return phase(id, { kind: "saved", note: plain(got.note) || w.saved(person.name, add) });
      }
      if (got.state === "failed") {
        await readList(); // the list may have changed under the person (someone else added or removed)
        return phase(id, { kind: "error", message: botWords(got.error, got.note) });
      }
      if (final) return gaveUp(id);
      phase(id, { kind: "saving", seen: got.state === "working" ? "working" : "waiting" });
    }
  }

  if (list === "loading")
    return (
      <p class="muted" role="status">
        {w.loading}
      </p>
    );
  if (list === "left")
    return (
      <p role="status" id="game-masters">
        {w.left}
      </p>
    );
  if (list === "failed")
    return (
      <p class="warn" role="alert">
        {w.failedToLoad}
      </p>
    );
  const full = list.gameMasters.length >= list.max;
  const row = (g: GameMaster) => {
    const p = phases[g.userId];
    const busy = p?.kind === "saving";
    return (
      <li key={g.userId}>
        <span class="row">
          <strong>{g.name}</strong>
          {g.plan && <span class="small muted">({w.plan})</span>}
          {g.you && <span class="small muted">({w.you})</span>}
          {!g.plan && p?.kind !== "asking" && (
            <button
              type="button"
              class="button secondary"
              aria-label={w.removeLabel(g.name)}
              aria-disabled={busy}
              onClick={() => !busy && phase(g.userId, { kind: "asking" })}
            >
              {w.remove}
            </button>
          )}
        </span>
        {g.plan && <span class="small muted block">{g.you ? w.planNoteSelf : w.planNote}</span>}
        {p?.kind === "asking" && (
          <div class="stack" role="group" aria-label={w.removeLabel(g.name)}>
            <p>{g.you ? w.confirmSelf : w.confirm(g.name)}</p>
            <span class="row">
              <button type="button" class="button secondary" onClick={() => phase(g.userId, null)}>
                {w.cancel}
              </button>
              <button type="button" class="button" onClick={() => void send(g, false)}>
                {w.confirmYes}
              </button>
            </span>
          </div>
        )}
        <Waiting phase={p} />
      </li>
    );
  };
  return (
    <section class="stack" aria-labelledby="game-masters-heading" id="game-masters">
      <h3 id="game-masters-heading">{w.heading}</h3>
      <p class="muted small">{w.lead(list.max)}</p>
      <ul class="campaigns">{list.gameMasters.map(row)}</ul>
      <h4>{w.addHeading}</h4>
      {full ? (
        <p class="small muted">{w.full(list.max)}</p>
      ) : (
        <div class="stack">
          <label class="stack">
            <span>{w.searchLabel}</span>
            <input type="search" value={query} onInput={(e) => setQuery((e.target as HTMLInputElement).value)} maxLength={60} autocomplete="off" />
          </label>
          {people === "loading" && (
            <p class="muted small" role="status">
              {query.trim() === "" ? w.loadingMembers : w.searching}
            </p>
          )}
          {people === "failed" && (
            <p class="warn small" role="alert">
              {w.searchFailed}
            </p>
          )}
          {Array.isArray(people) && people.length === 0 && <p class="small muted">{query.trim() === "" ? w.nobody : w.noMatch}</p>}
          <p class="small muted">{w.inDiscord}</p>
          {Array.isArray(people) && people.length > 0 && (
            <ul class="campaigns">
              {people.map((c) => {
                const p = phases[c.userId];
                const busy = p?.kind === "saving";
                return (
                  <li key={c.userId}>
                    <span class="row">
                      <strong>{c.name}</strong>
                      <button type="button" class="button" aria-label={w.addLabel(c.name)} aria-disabled={busy} onClick={() => !busy && void send(c, true)}>
                        {w.add}
                      </button>
                    </span>
                    <Waiting phase={p} />
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

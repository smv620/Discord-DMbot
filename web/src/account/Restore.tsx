/** @jsxImportSource preact */
// The Load a backup page (#2745; docs/PLAN.md, "Everything on the website except picking Discord channels"): a
// person picks the copy they downloaded, says whether it becomes a new campaign or replaces one of theirs, sends
// its transcripts files (all or none, as in Discord), sees what will happen, and presses Load it. DMbot checks the
// plan, the free campaign slot and the rights again when it loads the copy; this page only says what it is told.
// Nothing leaves for DMbot until Load it is pressed. The copy holds the campaign's secrets, so the page keeps only
// what it needs to show (the copy's name) and never anything from inside the file. Shown only while WEB_RESTORE_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { RESTORE_MAX_PARTS, restoreProblem, restoreWords as w } from "../content/restore-words";
import { WEB_RESTORE_LIVE } from "../content/restore-switch";
import { stageWords } from "../content/work-progress";
import { ApiError, type AccountApi, type Campaign, type Me, type RestoreStatus } from "./api";
import { campaignHref } from "./campaignLinks";
import { keepFocus } from "./focus";
import WorkProgress from "./WorkProgress";

const POLL_MS = 3000;
const IDLE_POLL_MAX_MS = 30_000; // while nothing moves the page asks less often
const WATCH_LIMIT_MS = 45 * 60_000; // then it stops looking and offers Check again

const ended = (s: RestoreStatus): boolean => s.state === "done" || s.state === "failed";

/** The person's own campaigns in a server that a copy could replace (the DMs' only; the API checks again). */
function replaceable(me: Me, serverId: string): Campaign[] {
  return me.campaigns.filter((c) => c.ref?.startsWith(`${serverId}.`) && c.role !== "player");
}

export default function Restore({ api }: { api: AccountApi }) {
  const [me, setMe] = useState<Me | null | "loading" | "failed">("loading");
  const [serverId, setServerId] = useState("");
  const [status, setStatus] = useState<RestoreStatus | null>(null);
  const [looked, setLooked] = useState(false); // the latest restore of this server has been looked up
  const [where, setWhere] = useState<"new" | "replace">("new");
  const [target, setTarget] = useState(""); // the campaign's ref to replace
  const [file, setFile] = useState<File | null>(null);
  const [parts, setParts] = useState<File[]>([]);
  const [without, setWithout] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); // what the page is doing, in words
  const [problem, setProblem] = useState<string | null>(null);
  const [frozen, setFrozen] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const live = useRef(true);
  const [done, setDone] = useState<string | null>(null); // a note that stays after the form comes back

  useEffect(
    () => () => {
      live.current = false;
    },
    [],
  );

  useEffect(() => {
    let current = true;
    api.me().then(
      (value) => {
        if (!current) return;
        setMe(value);
        const first = value?.servers.find((s) => s.hasDmbot);
        if (first) setServerId(first.id);
      },
      () => current && setMe("failed"),
    );
    return () => {
      current = false;
    };
  }, [api]);

  // The latest restore of the chosen server: a page that was closed finds its way back.
  useEffect(() => {
    if (!serverId || !api.restoreStatus) return;
    let current = true;
    setLooked(false);
    setStatus(null);
    setFrozen(false);
    api.restoreStatus(serverId).then(
      (found) => {
        if (!current) return;
        setStatus(found);
        setLooked(true);
      },
      () => current && setLooked(true),
    );
    return () => {
      current = false;
    };
  }, [api, serverId]);

  // While DMbot works on it, ask again (slower while nothing moves), and stop looking after a while.
  const watching = status !== null && !ended(status) && status.stage !== "restore_receiving";
  const stamp = status?.updatedAt ?? 0;
  useEffect(() => {
    if (!watching || !status || !api.restoreStatus || frozen) return;
    let current = true;
    let delay = POLL_MS;
    let last = stamp;
    const began = Date.now();
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (Date.now() - began > WATCH_LIMIT_MS) {
        setFrozen(true);
        return;
      }
      api.restoreStatus?.(serverId, status.id).then(
        (found) => {
          if (!current) return;
          if (found === null) {
            setStatus(null);
            setProblem(w.gone);
            return;
          }
          setStatus(found);
          const moved = (found.updatedAt ?? 0) !== last;
          last = found.updatedAt ?? 0;
          delay = moved ? POLL_MS : Math.min(delay * 2, IDLE_POLL_MAX_MS);
          if (!ended(found)) timer = setTimeout(tick, delay);
        },
        () => {
          if (!current) return;
          delay = Math.min(delay * 2, IDLE_POLL_MAX_MS);
          timer = setTimeout(tick, delay);
        },
      );
    };
    timer = setTimeout(tick, delay);
    return () => {
      current = false;
      clearTimeout(timer);
    };
    // (a new look at the same restore must not restart the clock: only a different restore does)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, serverId, watching, status?.id, frozen]);

  // Each screen starts at its heading, so a keyboard or screen-reader user is not left on a button that went.
  const screen = status === null ? "form" : status.stage === "restore_receiving" ? `receiving:${without}:${status.partsReceived}` : status.state;
  useEffect(() => {
    heading.current?.focus();
  }, [screen]);

  const fail = (error: unknown) => {
    setProblem(restoreProblem(error instanceof ApiError ? error.kind : "server"));
  };

  const read = async (id?: string): Promise<RestoreStatus | null> => {
    const found = (await api.restoreStatus?.(serverId, id)) ?? null;
    if (live.current) setStatus(found);
    return found;
  };

  if (me === "loading") return <p class="muted" role="status">{w.loading}</p>;
  if (me === "failed") return <p class="warn" role="alert">{w.failedToLoad}</p>;
  if (me === null) return <p role="status">{w.signedOut}</p>;
  if (!WEB_RESTORE_LIVE || !api.restoreStage) return <p class="note">{w.unavailable}</p>;
  const servers = me.servers.filter((s) => s.hasDmbot);
  if (servers.length === 0) return <p class="note">{w.noServers}</p>;
  const mine = replaceable(me, serverId);

  const check = async () => {
    if (!file || !api.restoreStage) {
      setProblem(w.pickFirst);
      return;
    }
    setProblem(null);
    setDone(null);
    setBusy(w.checking);
    try {
      const picked = where === "replace" ? mine.find((c) => c.ref === target) : undefined;
      const staged = await api.restoreStage(serverId, file, picked?.ref);
      setWithout(false);
      setParts([]);
      await read(staged.id);
    } catch (error) {
      // One of theirs is already on its way (slow-down): show that one.
      if (error instanceof ApiError && error.kind === "slow-down") await read().catch(() => null);
      fail(error);
    } finally {
      if (live.current) setBusy(null);
    }
  };

  const sendParts = async () => {
    if (!status || !api.restorePart) return;
    if (parts.length !== status.partsWanted) {
      setProblem(w.partsWrongCount(parts.length, status.partsWanted));
      return;
    }
    setProblem(null);
    try {
      for (const [i, part] of parts.entries()) {
        setBusy(w.partsSending(i + 1, parts.length));
        await api.restorePart(serverId, status.id, i + 1, part);
      }
      await read(status.id);
    } catch (error) {
      fail(error);
      await read(status.id).catch(() => null);
    } finally {
      if (live.current) setBusy(null);
    }
  };

  const load = async () => {
    if (!status || !api.restoreGo) return;
    setProblem(null);
    setBusy(w.working);
    try {
      await api.restoreGo(serverId, status.id, without);
      await read(status.id);
    } catch (error) {
      fail(error);
      await read(status.id).catch(() => null);
    } finally {
      if (live.current) setBusy(null);
    }
  };

  const clear = async () => {
    if (!status) return;
    setProblem(null);
    setBusy(w.working);
    let kept = false; // DMbot has it: the page goes on showing it
    try {
      await api.restoreCancel?.(serverId, status.id);
    } catch (error) {
      // Gone already is what was wanted. But DMbot may have started it meanwhile (another tab pressed Load
      // it): that can't be taken back, so say so and go on showing it.
      if (error instanceof ApiError && error.kind === "restore-working") {
        kept = true;
        fail(error);
        await read(status.id).catch(() => null);
      } else if (!(error instanceof ApiError && error.kind === "not-found")) {
        fail(error);
      }
    }
    if (!live.current) return;
    setBusy(null);
    if (kept) return;
    setDone(status.state === "running" ? w.cancelled : null);
    setStatus(null);
    setFile(null);
    setParts([]);
    setWithout(false);
    keepFocus(() => heading.current);
  };

  const picker = (
    <div class="stack">
      <label class="field">
        <span>{w.serverLabel}</span>
        <select
          value={serverId}
          disabled={busy !== null || (status !== null && !ended(status))}
          onChange={(e) => {
            setServerId((e.currentTarget as HTMLSelectElement).value);
            setProblem(null);
          }}
        >
          {servers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );

  const note = problem && (
    <p class="warn" role="alert" ref={(el) => el?.scrollIntoView?.({ block: "nearest" })}>
      {problem}
    </p>
  );

  // ---- no restore yet: the form ----
  if (status === null) {
    return (
      <div class="stack restore">
        <h2 ref={heading} tabIndex={-1}>
          {w.heading}
        </h2>
        <p>{w.intro}</p>
        {done && (
          <p class="ok" role="status">
            {done}
          </p>
        )}
        {picker}
        {!looked ? (
          <p class="muted" role="status">
            {w.loading}
          </p>
        ) : (
          <>
            <fieldset class="field">
              <legend>{w.whereLegend}</legend>
              <label>
                <input type="radio" name="where" checked={where === "new"} onChange={() => setWhere("new")} /> {w.asNew}
              </label>
              <label>
                <input
                  type="radio"
                  name="where"
                  checked={where === "replace"}
                  disabled={mine.length === 0}
                  onChange={() => {
                    setWhere("replace");
                    if (!target && mine[0]?.ref) setTarget(mine[0].ref);
                  }}
                />{" "}
                {w.asReplace}
              </label>
              {mine.length === 0 && <span class="small muted">{w.noneToReplace}</span>}
            </fieldset>
            {where === "replace" && mine.length > 0 && (
              <label class="field">
                <span>{w.replaceLabel}</span>
                <select value={target} onChange={(e) => setTarget((e.currentTarget as HTMLSelectElement).value)}>
                  {mine.map((c) => (
                    <option key={c.id} value={c.ref ?? ""}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label class="field">
              <span>{w.fileLabel}</span>
              <input
                type="file"
                accept=".gz,.json,application/gzip,application/json"
                disabled={busy !== null}
                aria-describedby="restore-file-hint"
                onChange={(e) => setFile((e.currentTarget as HTMLInputElement).files?.[0] ?? null)}
              />
              <span id="restore-file-hint" class="small muted">
                {w.fileHint}
              </span>
            </label>
            {note}
            {busy !== null && (
              <p role="status" class="muted">
                {busy}
              </p>
            )}
            <button type="button" class="button" aria-disabled={busy !== null || !file} onClick={() => busy === null && void check()}>
              {w.checkFile}
            </button>
          </>
        )}
      </div>
    );
  }

  // ---- the copy is staged, not yet sent ----
  if (status.stage === "restore_receiving") {
    const needFiles = status.partsWanted > 0 && !without && status.partsReceived < status.partsWanted;
    if (needFiles) {
      return (
        <div class="stack restore">
          <h2 ref={heading} tabIndex={-1}>
            {w.partsHeading(status.name)}
          </h2>
          <p>{w.partsText(status.partsWanted)}</p>
          <label class="field">
            <span>{w.partsLabel}</span>
            <input
              type="file"
              multiple
              accept=".gz,application/gzip"
              disabled={busy !== null}
              aria-describedby="restore-parts-count"
              onChange={(e) => setParts(Array.from((e.currentTarget as HTMLInputElement).files ?? []).slice(0, RESTORE_MAX_PARTS + 1))}
            />
            <span id="restore-parts-count" class="small muted" role="status">
              {w.partsPicked(parts.length, status.partsWanted)}
            </span>
          </label>
          {note}
          {busy !== null && (
            <p role="status" class="muted">
              {busy}
            </p>
          )}
          <div class="stack">
            <button type="button" class="button" aria-disabled={busy !== null || parts.length === 0} onClick={() => busy === null && parts.length > 0 && void sendParts()}>
              {w.sendParts}
            </button>
            <button type="button" class="button secondary" aria-disabled={busy !== null} onClick={() => busy === null && setWithout(true)}>
              {w.withoutParts}
            </button>
            <button type="button" class="button secondary" aria-disabled={busy !== null} onClick={() => busy === null && void clear()}>
              {w.cancel}
            </button>
          </div>
        </div>
      );
    }
    const target = status.replaceRef ? (me.campaigns.find((c) => c.ref === status.replaceRef) ?? null) : null;
    const serverName = servers.find((x) => x.id === serverId)?.name ?? "";
    // A replace whose campaign can't be named is never offered: the button would destroy something unnamed.
    const lost = status.replacing && target === null;
    return (
      <div class="stack restore">
        <h2 ref={heading} tabIndex={-1}>
          {w.confirmHeading}
        </h2>
        <p>{lost ? w.lostTrack : status.replacing ? w.confirmReplace(status.name, target?.name ?? "") : w.confirmNew(status.name, serverName)}</p>
        {without && status.partsWanted > 0 && <p class="note">{w.noTranscriptsNote}</p>}
        <p class="small muted">{w.expires}</p>
        {status.replacing && target?.ref && (
          <p>
            <a class="button secondary" href={campaignHref(target.ref)} target="_blank" rel="noopener">
              {w.downloadFirst}
            </a>{" "}
            <span class="small muted">{w.downloadNote}</span>
          </p>
        )}
        {note}
        {busy !== null && (
          <p role="status" class="muted">
            {busy} {w.keepOpen}
          </p>
        )}
        <div class="stack">
          {!lost && (
            <button type="button" class={status.replacing ? "button danger" : "button"} aria-disabled={busy !== null} onClick={() => busy === null && void load()}>
              {status.replacing ? w.yesReplace : w.yes}
            </button>
          )}
          <button type="button" class="button secondary" aria-disabled={busy !== null} onClick={() => busy === null && void clear()}>
            {w.cancel}
          </button>
        </div>
      </div>
    );
  }

  // ---- with DMbot, or ended ----
  const reason = status.reason ? (Object.hasOwn(w.reasons, status.reason) ? (w.reasons[status.reason] as string) : w.reasons["failed"]) : null;
  return (
    <div class="stack restore">
      <h2 ref={heading} tabIndex={-1}>
        {status.state === "done" ? w.doneTitle(status.name) : w.loadingHeading(status.name)}
      </h2>
      <WorkProgress
        stage={stageWords(w.stage, status.stage)}
        done={status.done}
        total={status.total}
        unit={status.unit ?? undefined}
        startedAt={status.startedAt}
        updatedAt={status.updatedAt}
        serverNow={status.serverNow}
        state={status.state}
        frozen={frozen}
        reason={status.state === "failed" ? reason : null}
      />
      {status.state === "done" && (
        <>
          <p>{status.replacing ? w.doneReplaced : w.doneNew}</p>
          {status.leftOut > 0 && <p class="note">{w.leftOut(status.leftOut)}</p>}
          {status.campaign && (
            <p>
              <a href={campaignHref(status.campaign)}>{w.openCampaign}</a>
            </p>
          )}
        </>
      )}
      {note}
      {frozen && <p class="small muted">{w.stopped}</p>}
      {frozen && (
        <button
          type="button"
          class="button secondary"
          onClick={() => {
            setFrozen(false);
            void read(status.id).catch(() => null);
          }}
        >
          {w.checkAgain}
        </button>
      )}
      {!ended(status) && status.stage === "restore_waiting" && (
        <button type="button" class="button secondary" aria-disabled={busy !== null} onClick={() => busy === null && void clear()}>
          {w.cancel}
        </button>
      )}
      {ended(status) && (
        <button type="button" class="button secondary" aria-disabled={busy !== null} onClick={() => busy === null && void clear()}>
          {w.startOver}
        </button>
      )}
    </div>
  );
}

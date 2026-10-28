/** @jsxImportSource preact */
// The "Share and follow" section of a campaign's House rules page (#2783; core web/rule_sharing.py): publish the
// campaign's house rules as a set other tables can follow, stop sharing, follow a set shared in the same server or
// by its share code (look first, then follow), and stop following. Every choice goes to the bot through a queue and
// waits for its answer, which applies the same rules as Discord's buttons (who may, the plan, the guess limit); the
// wait shows what DMbot is doing, how long it has run and when it last moved (#2564). For the campaign's game masters
// only: anyone else is refused and sees nothing. Share codes are never shown here; they are made and read in Discord
// (#2460). Loaded only on the House rules page (LazyPanel), shown only while RULE_SHARING_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { ruleSharingWords as w } from "../content/rule-sharing-words";
import {
  ApiError,
  type AccountApi,
  type RuleSharing,
  type RuleSharingAnswer,
  type RuleSharingChoice,
} from "./api";
import { GIVE_UP_MS, LATE_POLL_MS, OFFLINE_AFTER_MS, POLL_MS } from "./settings-timing";
import WorkProgress from "./WorkProgress";

type Report = { kind: "saved" | "error"; text: string } | null;
type Work = { answer: RuleSharingAnswer | null; gaveUp: boolean };

const problem = (kind: string): string => (Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
const ENDS = ["not-allowed", "not-found", "signed-out"]; // looking again will not mend these
/** Discord's own words come back with bold marks; the page shows them plain. */
const plain = (note: string): string => note.replaceAll("**", "");

export default function RuleSharingSection({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [info, setInfo] = useState<RuleSharing | "loading" | "failed" | "hidden">("loading");
  const [work, setWork] = useState<Work | null>(null);
  const [report, setReport] = useState<Report>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [found, setFound] = useState<{ code: string; note: string } | null>(null);
  const [asking, setAsking] = useState<string | null>(null); // the set in the list the person is about to follow
  const inFlight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const read = async (): Promise<void> => {
    if (!api.ruleSharing) return setInfo("failed");
    try {
      const got = await api.ruleSharing(campaignRef);
      if (live.current) setInfo(got);
    } catch (error) {
      // A player, or another campaign's game master: nothing to show, and no word about why.
      if (live.current && error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) return setInfo("hidden");
      if (live.current) setInfo((before) => (typeof before === "object" ? before : "failed"));
    }
  };
  useEffect(() => {
    void read();
  }, [api, campaignRef]);

  /** Send one choice and wait for the bot. Returns the final answer, or null when it failed or the page gave up
   * (the report says which). One at a time: a second press while one is on its way is ignored. */
  async function send(choice: RuleSharingChoice): Promise<RuleSharingAnswer | null> {
    if (inFlight.current) return null;
    inFlight.current = true;
    setReport(null);
    setAsking(null);
    setWork({ answer: null, gaveUp: false });
    const fail = (text: string): null => {
      if (live.current) {
        setWork(null);
        setReport({ kind: "error", text });
      }
      return null;
    };
    try {
      if (!api.chooseRuleSharing || !api.ruleSharingAnswer) return fail(problem("server"));
      let id: string;
      try {
        id = await api.chooseRuleSharing(campaignRef, choice);
      } catch (error) {
        return fail(problem(error instanceof ApiError ? error.kind : "server"));
      }
      const started = Date.now();
      for (let i = 0; live.current; i += 1) {
        const behind = Date.now() - started > OFFLINE_AFTER_MS;
        await new Promise((r) => setTimeout(r, behind ? LATE_POLL_MS : POLL_MS[Math.min(i, POLL_MS.length - 1)]));
        if (!live.current) return null;
        const final = Date.now() - started > GIVE_UP_MS;
        let got: RuleSharingAnswer;
        try {
          got = await api.ruleSharingAnswer(campaignRef, id);
        } catch (error) {
          if (error instanceof ApiError && ENDS.includes(error.kind)) return fail(problem(error.kind));
          if (!final) continue; // a look that fails is tried again until the hour is up
          setWork({ answer: null, gaveUp: true });
          await read();
          return null;
        }
        if (got.state === "done") {
          setWork(null);
          await read();
          return got;
        }
        if (got.state === "failed") {
          await read(); // it may have changed under the person
          return fail(plain(got.note) || problem(got.error || "failed"));
        }
        if (final) {
          setWork({ answer: got, gaveUp: true });
          await read();
          return null;
        }
        setWork({ answer: got, gaveUp: false });
      }
      return null;
    } finally {
      inFlight.current = false;
    }
  }

  const choose = async (choice: RuleSharingChoice): Promise<void> => {
    const got = await send(choice);
    if (!got || !live.current) return;
    if (choice.action === "look_code") return setFound({ code: choice.arg, note: plain(got.note) });
    setFound(null);
    if (choice.action === "publish") setName("");
    if (choice.action === "follow_code") setCode("");
    const text = plain(got.note) || (w.done[choice.action] as string);
    if (text) setReport({ kind: "saved", text });
  };

  if (info === "hidden") return null;
  if (info === "loading")
    return (
      <p class="muted" role="status">
        {w.loading}
      </p>
    );
  if (info === "failed")
    return (
      <p class="warn" role="alert">
        {w.failedToLoad}
      </p>
    );
  const busy = work !== null;
  const mine = info.published;
  const cleaned = code.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  return (
    <section class="stack" aria-labelledby="rule-sharing-heading" id="rule-sharing">
      <h3 id="rule-sharing-heading">{w.heading}</h3>
      <p class="small">{w.lead}</p>
      <p class={report?.kind === "saved" ? "" : "small muted"} role="status">
        {report?.kind === "saved" ? report.text : ""}
      </p>
      <p class="warn small" role="alert">
        {report?.kind === "error" ? report.text : ""}
      </p>
      {work && !work.gaveUp && (
        <WorkProgress
          stage={work.answer ? (w.stage[work.answer.stage] ?? (w.stage["sharing"] as string)) : (w.stage["waiting"] as string)}
          startedAt={work.answer?.startedAt}
          updatedAt={work.answer?.updatedAt}
          serverNow={work.answer?.serverNow}
          state={work.answer?.state === "stalled" ? "stalled" : "running"}
          whatToDo={w.quiet}
        />
      )}
      {work?.gaveUp && <WorkProgress stage={w.stage["waiting"] as string} state="stalled" frozen whatToDo={w.gaveUp} />}

      <h4>{w.shareHeading}</h4>
      {mine?.shared ? (
        <div class="stack">
          <p>{w.sharedAs(mine.name, mine.version)}</p>
          <p class="small muted">{w.stopNote}</p>
          <p class="small muted">{w.codesInDiscord}</p>
          <p>
            <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void choose({ action: "stop_sharing" })}>
              {w.stop}
            </button>
          </p>
        </div>
      ) : !info.canShare ? (
        <p class="small muted">{w.plan}</p>
      ) : info.ruleCount === 0 ? (
        <p class="small muted">{w.noRules}</p>
      ) : (
        <form
          class="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy && name.trim() !== "") void choose({ action: "publish", arg: name.trim() });
          }}
        >
          {mine && <p class="small muted">{w.notShared(mine.name)}</p>}
          <label class="stack">
            <span>{w.nameLabel}</span>
            <input type="text" value={name} maxLength={60} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
          </label>
          <p>
            <button type="submit" class="button" aria-disabled={busy || name.trim() === ""}>
              {w.share}
            </button>
          </p>
        </form>
      )}

      <h4>{w.followHeading}</h4>
      {info.following.length > 0 ? (
        <div class="stack">
          <ul class="campaigns">
            {info.following.map((f) => (
              <li key={`${f.name}-${f.version}`}>
                <span class="row">
                  <strong>{w.following(f.name, f.publisher, f.version)}</strong>
                  <button
                    type="button"
                    class="button secondary"
                    aria-label={w.stopFollowingLabel(f.name)}
                    aria-disabled={busy}
                    onClick={() => !busy && void choose({ action: "unfollow" })}
                  >
                    {w.stopFollowing}
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <p class="small muted">{w.followingOne}</p>
        </div>
      ) : (
        <div class="stack">
          <h5>{w.hereHeading}</h5>
          {info.here.length === 0 ? (
            <p class="small muted">{w.hereNone}</p>
          ) : (
            <ul class="campaigns">
              {info.here.map((h) => (
                <li key={h.setId}>
                  <span class="row">
                    <span>{w.hereLine(h.name, h.publisher, h.rules, h.version)}</span>
                    <button
                      type="button"
                      class="button secondary"
                      aria-label={w.followLabel(h.name)}
                      aria-disabled={busy}
                      onClick={() => !busy && setAsking(h.setId)}
                    >
                      {w.follow}
                    </button>
                  </span>
                  {asking === h.setId && (
                    <div class="stack" role="group" aria-label={w.followLabel(h.name)}>
                      <p>{w.confirmFollow(h.name, h.rules)}</p>
                      <span class="row">
                        <button type="button" class="button secondary" onClick={() => setAsking(null)}>
                          {w.cancel}
                        </button>
                        <button type="button" class="button" onClick={() => void choose({ action: "follow_here", arg: h.setId })}>
                          {w.confirmYes}
                        </button>
                      </span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          <h5>{w.codeHeading}</h5>
          <form
            class="stack"
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy && cleaned.length === 10) void choose({ action: "look_code", arg: cleaned });
            }}
          >
            <label class="stack">
              <span>{w.codeLabel}</span>
              <input
                type="text"
                value={code}
                maxLength={20}
                autocomplete="off"
                onInput={(e) => {
                  setCode((e.target as HTMLInputElement).value);
                  setFound(null);
                }}
              />
            </label>
            <p class="small muted">{w.codeHelp}</p>
            <p>
              <button type="submit" class="button secondary" aria-disabled={busy || cleaned.length !== 10}>
                {w.look}
              </button>
            </p>
          </form>
          {found && found.code === cleaned && (
            <div class="stack" role="group" aria-label={w.codeHeading}>
              <p>{found.note}</p>
              <p>
                <button type="button" class="button" aria-disabled={busy} onClick={() => !busy && void choose({ action: "follow_code", arg: found.code })}>
                  {w.foundFollow}
                </button>
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

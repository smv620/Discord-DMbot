/** @jsxImportSource preact */
// The optional rules section of a campaign's Settings tab (#2753; core web/optional_rules.py): the rules from
// other books that apply to the campaign's main rules, on or off, with the same words as Discord's
// `/dmbot optionalrules`. A switch sends the choice to the bot through the settings queue and waits for its
// answer. The wait shows what DMbot is doing (waiting to pick it up, or saving it), how long it has run and when it
// last moved (#2564), and says what to do when DMbot is quiet or has not answered in the hour. Loaded only when the
// game master opens the Settings tab (LazyPanel), and shown only while OPTIONAL_RULES_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { optionalWords as w } from "../content/optional-rules-words";
import { stageWords } from "../content/work-progress";
import { ApiError, type AccountApi, type OptionalRule, type OptionalRules } from "./api";
import { GIVE_UP_MS, LATE_POLL_MS, OFFLINE_AFTER_MS, POLL_MS } from "./settings-timing";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

type Phase =
  | { kind: "saving"; seen: "waiting" | "working" }
  | { kind: "gaveup" }
  | { kind: "saved"; note: string }
  | { kind: "error"; message: string };

const problem = (kind: string): string => (Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
const ENDS = ["not-allowed", "not-found", "signed-out"]; // looking again will not mend these
/** What the bot says when it cannot do it, in the page's own words (a refusal it cannot mend is not "try again"). */
const botWords = (error: string): string =>
  error === "not_allowed" ? problem("not-allowed") : error === "not_found" ? problem("not-found") : error === "bad_value" ? problem("bad-value") : problem("server");

function Row({ rule, phase, onSwitch }: { rule: OptionalRule; phase: Phase | undefined; onSwitch: () => void }) {
  const saving = phase?.kind === "saving";
  const seen = useObservedWait(saving, saving ? phase.seen : "");
  return (
    <li>
      <span class="block">
        <strong>{rule.name}</strong>: {rule.summary}
      </span>
      <span class="row">
        <button
          type="button"
          class={rule.on ? "button" : "button secondary"}
          role="switch"
          aria-checked={rule.on}
          aria-label={rule.name}
          aria-disabled={saving}
          onClick={() => !saving && onSwitch()}
        >
          {rule.on ? w.on : w.off}
        </button>
        <span class="small muted">{rule.changed ? w.chosen : w.followsDefault}</span>
      </span>
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
      <p class="small muted" role="status">
        {phase?.kind === "saved" ? phase.note : ""}
      </p>
      <p class="warn small" role="alert">
        {phase?.kind === "error" ? phase.message : ""}
      </p>
    </li>
  );
}

export default function OptionalRulesSection({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<OptionalRules | "loading" | "failed">("loading");
  const [phases, setPhases] = useState<Record<string, Phase>>({});
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    if (!api.campaignOptionalRules) return setList("failed");
    api.campaignOptionalRules(campaignRef).then(
      (found) => live.current && setList(found),
      () => live.current && setList("failed"),
    );
  }, [api, campaignRef]);

  const phase = (id: string, next: Phase | null) =>
    live.current &&
    setPhases((before) => {
      const { [id]: _gone, ...rest } = before;
      return next ? { ...rest, [id]: next } : rest;
    });

  /** The bot has not answered in the hour the queue keeps a choice: it may still be applied later, so the list is
   * read again (it shows where things stand) and the page says so, not that nothing was saved. */
  async function gaveUp(rule: OptionalRule) {
    phase(rule.id, { kind: "gaveup" });
    const found = await api.campaignOptionalRules?.(campaignRef).catch(() => null);
    if (found && live.current) setList(found);
  }

  async function send(rule: OptionalRule, on: boolean) {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return phase(rule.id, { kind: "error", message: problem("server") });
    phase(rule.id, { kind: "saving", seen: "waiting" });
    let answer: string;
    try {
      answer = await api.setCampaignSetting(campaignRef, { setting: "optional_rule", value: rule.id, on });
    } catch (error) {
      return phase(rule.id, { kind: "error", message: problem(error instanceof ApiError ? error.kind : "server") });
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
        if (error instanceof ApiError && ENDS.includes(error.kind)) return phase(rule.id, { kind: "error", message: problem(error.kind) });
        if (final) return gaveUp(rule);
        continue; // a look that fails is tried again until the hour is up
      }
      if (got.state === "done") {
        setList((before) =>
          typeof before === "object"
            ? { ...before, rules: before.rules.map((r) => (r.id === rule.id ? { ...r, on, changed: true } : r)) }
            : before,
        );
        return phase(rule.id, { kind: "saved", note: got.note || w.saved(rule.name, on) });
      }
      if (got.state === "failed") return phase(rule.id, { kind: "error", message: botWords(got.error) });
      if (final) return gaveUp(rule);
      phase(rule.id, { kind: "saving", seen: got.state === "working" ? "working" : "waiting" });
    }
  }

  if (list === "loading")
    return (
      <p class="muted" role="status">
        {w.loading}
      </p>
    );
  if (list === "failed")
    return (
      <p class="warn" role="alert">
        {w.failedToLoad}
      </p>
    );
  let book = "";
  return (
    <section class="stack" aria-labelledby="optional-rules-heading" id="optional-rules">
      <h3 id="optional-rules-heading">{w.heading}</h3>
      {list.rules.length === 0 ? (
        <p>{w.none(list.main)}</p>
      ) : (
        <>
          <p class="muted small">{w.lead(list.main)}</p>
          <ul class="campaigns">
            {list.rules.map((rule) => {
              const heading = rule.book !== book ? ((book = rule.book), rule.book) : null;
              return (
                <>
                  {heading && (
                    <li key={`${rule.id}-book`} class="campaign-name">
                      {heading}
                    </li>
                  )}
                  <Row key={rule.id} rule={rule} phase={phases[rule.id]} onSwitch={() => void send(rule, !rule.on)} />
                </>
              );
            })}
          </ul>
          {list.note && <p class="small muted">{list.note}</p>}
          <p class="small muted">{w.disclaimer}</p>
        </>
      )}
    </section>
  );
}

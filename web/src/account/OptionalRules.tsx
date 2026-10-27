/** @jsxImportSource preact */
// The optional rules section of a campaign's Settings tab (#2753; core web/optional_rules.py): the rules from
// other books that apply to the campaign's main rules, on or off, with the same words as Discord's
// `/dmbot optionalrules`. A switch sends the choice to the bot through the settings queue and waits for its
// answer, as the other choices do. Loaded only when the game master opens the Settings tab (LazyPanel), and
// shown only while OPTIONAL_RULES_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { optionalWords as w } from "../content/optional-rules-words";
import { ApiError, type AccountApi, type OptionalRule, type OptionalRules } from "./api";
import { GIVE_UP_MS, LATE_POLL_MS, OFFLINE_AFTER_MS, POLL_MS, SLOW_AFTER_MS } from "./settings-timing";

type Phase = { kind: "saving"; slow: boolean } | { kind: "offline" } | { kind: "saved"; note: string } | { kind: "error"; message: string };

const problem = (kind: string): string => (Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
const ENDS = ["not-allowed", "not-found", "signed-out"]; // looking again will not mend these

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

  async function send(rule: OptionalRule, on: boolean) {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return phase(rule.id, { kind: "error", message: w.problems["server"] as string });
    phase(rule.id, { kind: "saving", slow: false });
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
      const waited = Date.now() - started;
      const final = waited > GIVE_UP_MS;
      // What the page says while it waits, whether the look worked or not: a site that cannot be reached
      // must not leave "Saving…" showing for an hour.
      const say = () => {
        if (waited > OFFLINE_AFTER_MS) phase(rule.id, { kind: "offline" });
        else if (waited > SLOW_AFTER_MS) phase(rule.id, { kind: "saving", slow: true });
      };
      let got;
      try {
        got = await api.campaignSettingAnswer(campaignRef, answer);
      } catch (error) {
        if (error instanceof ApiError && ENDS.includes(error.kind)) return phase(rule.id, { kind: "error", message: problem(error.kind) });
        if (final) return gaveUp(rule);
        say();
        continue;
      }
      if (got.state === "done") {
        setList((before) =>
          typeof before === "object"
            ? { ...before, rules: before.rules.map((r) => (r.id === rule.id ? { ...r, on, changed: true } : r)) }
            : before,
        );
        return phase(rule.id, { kind: "saved", note: got.note || w.saved(rule.name, on) });
      }
      if (got.state === "failed") return phase(rule.id, { kind: "error", message: got.error === "bad_value" ? problem("bad-value") : problem("server") });
      if (final) return gaveUp(rule);
      say();
    }
  }

  /** The bot has not answered in the hour the queue keeps a choice: the choice may still be applied later, so
   * the list is read again (it shows where things stand) and the page says so, not that nothing was saved. */
  async function gaveUp(rule: OptionalRule) {
    phase(rule.id, { kind: "error", message: w.timedOut });
    const found = await api.campaignOptionalRules?.(campaignRef).catch(() => null);
    if (found && live.current) setList(found);
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
              const state = phases[rule.id];
              const busy = state?.kind === "saving" || state?.kind === "offline"; // a second tap would queue a second change
              return (
                <li key={rule.id}>
                  {heading && <p class="campaign-name">{heading}</p>}
                  <span class="block">
                    <strong>{rule.name}</strong>: {rule.summary}
                  </span>
                  <span class="row">
                    <button
                      type="button"
                      class={rule.on ? "button" : "button secondary"}
                      role="switch"
                      aria-checked={rule.on}
                      aria-label={rule.on ? w.turnOff(rule.name) : w.turnOn(rule.name)}
                      aria-disabled={busy}
                      onClick={() => !busy && void send(rule, !rule.on)}
                    >
                      {rule.on ? w.on : w.off}
                    </button>
                    <span class="small muted">{rule.changed ? w.chosen : w.followsDefault}</span>
                  </span>
                  <p class="small muted" role="status">
                    {state?.kind === "saving" ? (state.slow ? `${w.saving} ${w.slow}` : w.saving) : state?.kind === "offline" ? w.offline : state?.kind === "saved" ? state.note : ""}
                  </p>
                  <p class="warn small" role="alert">
                    {state?.kind === "error" ? state.message : ""}
                  </p>
                </li>
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

/** @jsxImportSource preact */
// Which of two house rules that disagree wins, as the DM decided (#3697; core #3777): the decided pairs with the two rules'
// words, "Rule N wins instead" and "Clear this choice". The choices go through the House rules page's own sharing queue (the
// bot applies them with the same store calls as Discord's card and checks the DM again), so this file only lists and asks.
// Only the campaign's own rules can be changed here: a pair with a rule from a followed set is shown, read-only, with a
// pointer to Discord. DMbot never picks a winner; the words are the DM's own rules, read from the House rules page.
import { useEffect, useState } from "preact/hooks";

import { ruleSharingWords as w } from "../content/rule-sharing-words";
import { ApiError, type AccountApi, type HouseRuleItem, type RuleDecision, type RuleSharingChoice, type RuleSide } from "./api";

const own = (s: RuleSide): boolean => s.set === "";
const day = (at: number): string => new Date(at * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });

export default function RuleDecisions({
  api,
  campaignRef,
  rules,
  busy,
  tick,
  choose,
}: {
  api: AccountApi;
  campaignRef: string;
  /** The campaign's own rules, from the House rules read (the words of each number). */
  rules: HouseRuleItem[];
  /** A sharing choice is on its way: no second press. */
  busy: boolean;
  /** Bumped after each change, so the list is read again. */
  tick: number;
  choose: (choice: RuleSharingChoice) => Promise<void>;
}) {
  const [list, setList] = useState<RuleDecision[] | "loading" | "failed" | "hidden">("loading");
  useEffect(() => {
    let live = true;
    if (!api.ruleDecisions) return void setList("hidden");
    api.ruleDecisions(campaignRef).then(
      (got) => live && setList(got),
      (error: unknown) => live && setList(error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found") ? "hidden" : "failed"),
    );
    return () => {
      live = false;
    };
  }, [api, campaignRef, tick]);

  if (list === "hidden") return null;
  const wordsOf = (s: RuleSide): string | null => (own(s) ? (rules.find((r) => r.number === s.number)?.rule ?? null) : null);
  const name = (s: RuleSide): string => (own(s) ? w.clashOwn(s.number) : w.clashOther(s.number, s.set));
  // (a followed set's rule words are not sent, so only its number and set are named)
  const line = (s: RuleSide): string => {
    const said = wordsOf(s);
    return said === null ? name(s) : `${name(s)}: ${said}`;
  };
  return (
    <section class="stack" aria-labelledby="rule-clashes-heading">
      <h4 id="rule-clashes-heading">{w.clashHeading}</h4>
      <p class="small">{w.clashLead}</p>
      {list === "loading" && (
        <p class="muted" role="status">
          {w.clashLoading}
        </p>
      )}
      {list === "failed" && (
        <p class="warn" role="alert">
          {w.clashFailed}
        </p>
      )}
      {Array.isArray(list) && list.length === 0 && <p class="small muted">{w.clashNone}</p>}
      {Array.isArray(list) && list.length > 0 && (
        <ul class="stack">
          {list.map((d) => {
            const win = d.winner === "a" ? d.a : d.b;
            const lose = d.winner === "a" ? d.b : d.a;
            const mine = own(d.a) && own(d.b);
            return (
              <li key={`${d.a.set}:${d.a.number}-${d.b.set}:${d.b.number}`} class="stack">
                <p>
                  <strong>{w.clashWins(name(win), name(lose))}</strong>
                </p>
                <ul class="small">
                  <li>{line(win)}</li>
                  <li>{line(lose)}</li>
                </ul>
                <p class="small muted">{w.clashDecided(day(d.decidedAt))}</p>
                {mine ? (
                  <p class="row">
                    <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void choose({ action: "decide_clash", arg: `${lose.number}:${win.number}` })}>
                      {w.clashFlip(lose.number)}
                    </button>
                    <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void choose({ action: "clear_clash", arg: `${d.a.number}:${d.b.number}` })}>
                      {w.clashClear}
                    </button>
                    <span class="small muted">{w.clashClearHint}</span>
                  </p>
                ) : (
                  <p class="small muted">{w.clashSetNote}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

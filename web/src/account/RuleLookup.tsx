/** @jsxImportSource preact */
// "Look up a rule" on a campaign's page (#5156): a game master types a rule, spell or creature and reads the same card as
// `/dmbot rule`, with its source. Nothing is kept: the answer goes when the campaign changes. The box is shown by the page
// to game masters only, behind RULE_LOOKUP_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { ruleLookupWords as words } from "../content/rule-lookup-words";
import { ApiError, type AccountApi, type RuleCard } from "./api";
import DiscordText from "./discord-text";
import SavingWait from "./SavingWait";

const MAX_CLOSE = 10;

export default function RuleLookup({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<RuleCard | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // A second press while one is with DMbot is ignored; a look that was asked for an earlier campaign is dropped.
  const flight = useRef(false);
  const live = useRef(true);
  const now = useRef(campaignRef);
  now.current = campaignRef;
  useEffect(() => () => void (live.current = false), []);
  useEffect(() => {
    setAnswer(null);
    setValue("");
    setProblem(null);
  }, [campaignRef]);

  async function look(name: string): Promise<void> {
    if (flight.current || !api.ruleLookup) return;
    const asked = campaignRef;
    flight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      const got = await api.ruleLookup(asked, name.trim());
      if (!live.current || now.current !== asked) return;
      setAnswer(got);
    } catch (error) {
      if (live.current && now.current === asked) {
        setAnswer(null);
        const kind = error instanceof ApiError ? error.kind : "";
        setProblem(kind === "not-found" ? words.notFound : kind === "bad-text" ? words.badText : kind === "too-soon" ? words.tooSoon : words.failed);
      }
    } finally {
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  return (
    <section class="stack" aria-labelledby="rule-lookup-heading">
      <h3 id="rule-lookup-heading">{words.heading}</h3>
      <form
        class="row"
        onSubmit={(event) => {
          event.preventDefault();
          void look(value);
        }}
      >
        <label class="stack" for="rule-lookup-box">
          {words.label}
          <input id="rule-lookup-box" type="text" maxLength={100} value={value} onInput={(event) => setValue((event.target as HTMLInputElement).value)} />
        </label>
        <button type="submit" class="button" disabled={busy}>
          {words.button}
        </button>
      </form>
      <SavingWait active={busy} stage={words.stage} whatToDo={words.slow} />
      {problem && (
        <p role="alert" class="warn">
          {problem}
        </p>
      )}
      {answer &&
        answer.parts.map((part, i) => (
          <div key={i} class="stack" style={{ whiteSpace: "pre-wrap" }}>
            <p>
              <DiscordText text={part} />
            </p>
          </div>
        ))}
      {answer?.kind === "none" && answer.close.length > 0 && (
        <div class="stack">
          <p>{words.didYouMean}</p>
          <div class="row">
            {answer.close.slice(0, MAX_CLOSE).map((name) => (
              <button
                key={name}
                type="button"
                class="button secondary"
                disabled={busy}
                onClick={() => {
                  setValue(name);
                  void look(name);
                }}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

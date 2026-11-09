/** @jsxImportSource preact */
// The start-up wizard's shell (#3956; docs/PLAN.md, "The campaign start-up wizard"): ten steps, one page each, with Back, Next and
// Skip on every page. Each step saves to the draft, so a reload or a later visit picks up where the person was. Skip asks only for
// the name and takes the defaults. The last page makes the campaign (the bot does the work in stages) and shows the shared wait panel.
import { useEffect, useRef, useState } from "preact/hooks";

import { WIZARD_STEPS, wizardWords as w } from "../content/wizard-words";
import { ConfirmStep, DataStep, DocsStep, PlayersStep, RulesStep, WhereStep, type StepProps } from "./WizardSteps";
import { WizardError, type WizardAnswers, type WizardApi, type WizardDraft, type WizardFinish } from "./wizard-api";
import WorkProgress from "./WorkProgress";

const STALLED_AFTER = 120; // seconds without a move before the wait says it has stalled
const POLL_MS = 2000;

const problemWords = (error: unknown): string => {
  if (error instanceof WizardError) {
    if (error.kind === "no_room") return w.noRoom;
    if (error.kind === "too_fast") return w.tooFast;
    if (error.kind === "expired") return w.expired;
    if (error.kind === "not_allowed") return w.notAllowed;
    if (error.kind === "rights_needed") return w.docs.rightsNeeded;
    if (error.kind === "bad_value" && error.key === "fallback") return w.rules.same;
  }
  return w.failed;
};

/** Where the campaign's page is, from the ref the finish run gives (`<server>.<campaign>`). */
const campaignHref = (ref: string): string => `/account/campaigns#${encodeURIComponent(ref)}`;

export default function Wizard({ api, guild }: { api: WizardApi; guild: string }) {
  const [draft, setDraft] = useState<WizardDraft | null | "loading">("loading");
  const [problem, setProblem] = useState("");
  const [skipping, setSkipping] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const seq = useRef(0); // the newest save: an older answer arriving late is ignored, so the page never jumps back
  const latest = useRef<WizardDraft | null>(null);
  const lastStep = useRef(0);
  useEffect(() => () => void (live.current = false), []);
  if (draft !== "loading" && draft !== null) latest.current = draft;

  // Move focus to the new page's heading when the step changes, so a screen reader hears where it is.
  const stepNow = draft !== "loading" && draft !== null ? draft.step : 0;
  useEffect(() => {
    if (stepNow && lastStep.current && stepNow !== lastStep.current) document.querySelector<HTMLElement>("section h2")?.focus();
    lastStep.current = stepNow;
  }, [stepNow]);

  useEffect(() => {
    api.get(guild).then(
      (got) => live.current && setDraft(got),
      () => live.current && setProblem(w.failed),
    );
  }, [api, guild]);

  if (problem && draft === "loading") return <p role="alert">{problem}</p>;
  if (draft === "loading") return <p class="muted" role="status">{w.loading}</p>;

  async function start() {
    setProblem("");
    try {
      setDraft(await api.start(guild, ""));
    } catch (error) {
      setProblem(problemWords(error));
    }
  }
  if (draft === null) {
    return (
      <section>
        <p>{w.startLead}</p>
        <button type="button" class="button" onClick={() => void start()}>
          {w.start}
        </button>
        {problem && <p role="alert">{problem}</p>}
      </section>
    );
  }

  async function discard() {
    try {
      await api.discard(guild);
      if (live.current) (setDiscarding(false), setDraft(null));
    } catch {
      if (live.current) (setDiscarding(false), setProblem(w.failed));
    }
  }

  const current: WizardDraft = draft;
  const step = Math.min(Math.max(current.step, 1), WIZARD_STEPS.length);
  const id = WIZARD_STEPS[step - 1]!;

  /** One save at a time in effect: the newest answer wins, and the buttons wait while one is out. */
  async function save(to: number | null, patch: Partial<WizardAnswers> | ((latest: WizardAnswers) => Partial<WizardAnswers>)) {
    const mine = ++seq.current;
    setBusy(true);
    setProblem("");
    try {
      const base = latest.current;
      const body = typeof patch === "function" ? patch(base ? base.answers : current.answers) : patch;
      const next = await api.set(guild, to ?? base?.step ?? step, body);
      if (live.current && mine === seq.current) setDraft(next);
    } catch (error) {
      if (live.current && mine === seq.current) setProblem(problemWords(error));
    } finally {
      if (live.current && mine === seq.current) setBusy(false);
    }
  }
  const go = (to: number) => save(to, {});
  const props: StepProps = {
    api,
    guild,
    draft: current,
    onPatch: (patch) => save(null, patch),
    onDraft: (next) => setDraft(next),
  };

  return (
    <div>
      <p class="small" role="status">
        {w.step(step, WIZARD_STEPS.length)}
      </p>
      {id === "where" && <WhereStep {...props} />}
      {id === "confirm" && <ConfirmStep {...props} />}
      {id === "players" && <PlayersStep {...props} />}
      {id === "lore" && <DocsStep {...props} slot="lore" heading={w.lore.heading} lead={w.lore.lead} />}
      {id === "secrets" && <DocsStep {...props} slot="secrets" heading={w.secrets.heading} lead={w.secrets.lead} />}
      {id === "rules" && <RulesStep {...props} />}
      {id === "house" && <DocsStep {...props} slot="house" heading={w.house.heading} lead={w.house.lead} />}
      {id === "homebrew" && <DocsStep {...props} slot="homebrew" heading={w.homebrew.heading} lead={w.homebrew.lead} />}
      {id === "data" && <DataStep {...props} />}
      {id === "finish" && <FinishStep {...props} />}
      {problem && <p role="alert">{problem}</p>}

      {!skipping && !discarding && (
        <div class="row">
          <button type="button" class="button secondary" disabled={busy || step === 1} onClick={() => void go(step - 1)}>
            {w.back}
          </button>
          {step < WIZARD_STEPS.length && (
            <button type="button" class="button" disabled={busy} onClick={() => void go(step + 1)}>
              {w.next}
            </button>
          )}
          {step < WIZARD_STEPS.length && (
            <button type="button" class="button secondary" disabled={busy} onClick={() => setSkipping(true)}>
              {w.skip}
            </button>
          )}
          <button type="button" class="link" disabled={busy} onClick={() => setDiscarding(true)}>
            {w.discard}
          </button>
        </div>
      )}

      {skipping && <SkipPanel {...props} step={step} onCancel={() => setSkipping(false)} onMade={(next) => (setDraft(next), setSkipping(false))} />}
      {discarding && (
        <div role="alertdialog" aria-label={w.discardAsk}>
          <p>{w.discardAsk}</p>
          <div class="row">
            <button type="button" class="button" onClick={() => void discard()}>
              {w.discardYes}
            </button>
            <button type="button" class="button secondary" onClick={() => setDiscarding(false)}>
              {w.discardNo}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Skip, with PLAN's words: it asks only for the name, then the defaults apply and the campaign is made. */
function SkipPanel({ api, guild, draft, step, onCancel, onMade }: StepProps & { step: number; onCancel(): void; onMade(next: WizardDraft): void }) {
  const [name, setName] = useState(draft.answers.name);
  const [problem, setProblem] = useState("");
  const [working, setWorking] = useState(false);
  async function skipAndMake() {
    if (working) return;
    setWorking(true);
    setProblem("");
    try {
      const next = await api.set(guild, 10, { name: name.trim() });
      await api.skip(guild, step);
      await api.finish(guild);
      onMade(next);
    } catch (error) {
      setWorking(false);
      setProblem(error instanceof WizardError && error.kind === "rights_needed" ? w.skipRights : problemWords(error));
    }
  }
  return (
    <div role="alertdialog" aria-label={w.skipAsk}>
      <p>{w.skipSays}</p>
      <label>
        {w.skipName}
        <input type="text" maxLength={100} value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      </label>
      <div class="row">
        <button type="button" class="button" disabled={working || !name.trim()} onClick={() => void skipAndMake()}>
          {w.skipYes}
        </button>
        <button type="button" class="button secondary" onClick={onCancel}>
          {w.skipNo}
        </button>
      </div>
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}

/** The last page: a summary, the press, and the wait while the bot makes the campaign in stages. */
function FinishStep({ api, guild, draft }: StepProps) {
  const t = w.finish;
  const [run, setRun] = useState<WizardFinish | null>(null);
  const [problem, setProblem] = useState("");
  const [started, setStarted] = useState(false);
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  // Follow a finish that is already going (a reload, or Skip pressed on another page).
  useEffect(() => {
    void api.finishState(guild).then((got) => got && live.current && (setRun(got), setStarted(true)), () => undefined);
  }, [api, guild]);

  // Poll until it is done or failed. One failed look is tried again; five in a row say the wait has stopped.
  const misses = useRef(0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!started || (run && (run.state === "done" || run.state === "failed"))) return;
    const timer = setTimeout(() => {
      api.finishState(guild).then(
        (got) => {
          if (!live.current) return;
          misses.current = 0;
          setRun(got);
          setTick((n) => n + 1);
        },
        () => {
          if (!live.current) return;
          misses.current += 1;
          if (misses.current >= 5) {
            const now = Math.floor(Date.now() / 1000);
            setRun((old) => ({ state: "failed", stage: old?.stage ?? "", part: old?.part ?? 0, ofParts: old?.ofParts ?? 0, error: "lost", campaignRef: old?.campaignRef ?? "", createdAt: old?.createdAt ?? now, movedAt: old?.movedAt ?? now, serverNow: now }));
          } else setTick((n) => n + 1);
        },
      );
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [api, guild, started, run, tick]);

  const [pressing, setPressing] = useState(false);
  async function press() {
    if (pressing) return;
    setPressing(true);
    setProblem("");
    misses.current = 0;
    try {
      await api.finish(guild);
      setRun(null); // (a retry starts from a fresh look, not the old failed row)
      setStarted(true);
      setTick((n) => n + 1);
    } catch (error) {
      setProblem(problemWords(error));
    } finally {
      if (live.current) setPressing(false);
    }
  }

  if (run && run.state === "done") {
    return (
      <section>
        <h2>{t.done}</h2>
        <a class="button" href={run.campaignRef ? campaignHref(run.campaignRef) : "/account/campaigns"}>{t.open}</a>
      </section>
    );
  }
  if (started) {
    const failed = run?.state === "failed";
    const stalled = run ? run.serverNow - run.movedAt > STALLED_AFTER : false;
    return (
      <section>
        <h2>{t.heading}</h2>
        {run && (
          <WorkProgress
            stage={failed ? t.errors[run.error] ?? t.errors["failed"]! : t.stages[run.stage] ?? t.stages["campaign"]!}
            done={run.ofParts > 0 ? run.part : null}
            total={run.ofParts > 0 ? run.ofParts : null}
            startedAt={run.createdAt}
            updatedAt={run.movedAt}
            serverNow={run.serverNow}
            state={failed ? "failed" : stalled ? "stalled" : "running"}
            frozen={failed}
            reason={failed ? t.errors[run.error] ?? t.errors["failed"]! : null}
          />
        )}
        {!run && <p class="muted" role="status">{w.loading}</p>}
        {run && !failed && <p class="small">{t.quiet}</p>}
        {failed && run && run.campaignRef && <a class="button secondary" href={campaignHref(run.campaignRef)}>{t.open}</a>}
        {failed && run && run.error !== "no-key" && (
          <button type="button" class="button" disabled={pressing} onClick={() => void press()}>
            {t.retry}
          </button>
        )}
        {problem && <p role="alert">{problem}</p>}
      </section>
    );
  }
  const a = draft.answers;
  return (
    <section>
      <h2>{t.heading}</h2>
      <p>{t.lead}</p>
      <ul>
        <li>
          {t.summaryName}: {a.name || "—"}
        </li>
        <li>
          {t.summaryRules}: {a.target === "2024" ? w.rules.current : w.rules.legacy}
        </li>
        <li>{t.summaryFiles(draft.documents.length)}</li>
        {a.skipped.length > 0 && <li>{t.summarySkipped}</li>}
      </ul>
      <button type="button" class="button" disabled={pressing || !a.name.trim()} onClick={() => void press()}>
        {t.button}
      </button>
      {problem && <p role="alert">{problem}</p>}
    </section>
  );
}

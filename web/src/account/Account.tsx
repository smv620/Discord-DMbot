/** @jsxImportSource preact */
// The signed-in area (#434): one island on /account. All data comes from the web API in
// core (#435); this file only shows it and sends the user's choices back.
import { useCallback, useEffect, useState } from "preact/hooks";

import {
  hoursLeftLine,
  hoursUsedLine,
  planName,
  text,
} from "../content/account";
import { formatPrice, plans as paidPlans, type PlanId } from "../content/pricing";
import { ApiError, type AccountApi, type Campaign, type Me, type Person } from "./api";

type State =
  | { kind: "loading" }
  | { kind: "down" }
  | { kind: "signed-out"; notice: string | null }
  | { kind: "signed-in"; me: Me }
  | { kind: "deleted" };

interface Props {
  api: AccountApi;
  /** Where to send the browser (checkout, billing portal, Discord). Swappable for tests. */
  go?: (url: string) => void;
  /** The page's query string, for the sign-in result (?signin=failed). */
  search?: string;
}

const defaultGo = (url: string): void => {
  window.location.assign(url);
};

export default function Account({ api, go = defaultGo, search = "" }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const me = await api.me();
      const failed = new URLSearchParams(search).get("signin") === "failed";
      setState(
        me
          ? { kind: "signed-in", me }
          : { kind: "signed-out", notice: failed ? text.signInFailed : null },
      );
    } catch {
      setState({ kind: "down" });
    }
  }, [api, search]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Run an action; on failure show a plain message instead of breaking the page. */
  const act = useCallback(
    async (action: () => Promise<void>, onError?: (error: ApiError) => string | null) => {
      setNotice(null);
      try {
        await action();
      } catch (error) {
        const apiError = error instanceof ApiError ? error : new ApiError("server");
        if (apiError.kind === "signed-out") {
          setState({ kind: "signed-out", notice: text.signedOutNow });
          return;
        }
        setNotice(onError?.(apiError) ?? text.actionFailed);
      }
    },
    [],
  );

  switch (state.kind) {
    case "loading":
      return <p class="muted" role="status">{text.loading}</p>;
    case "down":
      return (
        <div class="panel" role="alert">
          <p>{text.down}</p>
          <button type="button" class="button" onClick={() => void load()}>
            {text.tryAgain}
          </button>
        </div>
      );
    case "signed-out":
      return <SignedOut api={api} notice={state.notice} />;
    case "deleted":
      return (
        <p class="panel" role="status">
          {text.deleted}
        </p>
      );
    case "signed-in":
      return (
        <SignedIn
          me={state.me}
          api={api}
          go={go}
          act={act}
          notice={notice}
          reload={load}
          onDeleted={() => setState({ kind: "deleted" })}
          onSignedOut={() => setState({ kind: "signed-out", notice: null })}
        />
      );
  }
}

function SignedOut({ api, notice }: { api: AccountApi; notice: string | null }) {
  return (
    <div class="stack">
      {notice && (
        <p class="panel warn" role="alert">
          {notice}
        </p>
      )}
      <p class="lead">{text.signInLead}</p>
      <a class="button" href={api.signInUrl()}>
        {text.signIn}
      </a>
      <p class="muted small">{text.signInNote}</p>
      <p>
        {text.newHere} <a href="/pricing">{text.seePrices}</a>
      </p>
    </div>
  );
}

type Act = (
  action: () => Promise<void>,
  onError?: (error: ApiError) => string | null,
) => Promise<void>;

interface SignedInProps {
  me: Me;
  api: AccountApi;
  go: (url: string) => void;
  act: Act;
  notice: string | null;
  reload: () => Promise<void>;
  onDeleted: () => void;
  onSignedOut: () => void;
}

function SignedIn({ me, api, go, act, notice, reload, onDeleted, onSignedOut }: SignedInProps) {
  return (
    <div class="stack">
      <div class="hello">
        <p class="lead">{text.greeting(me.user.name)}</p>
        <button
          type="button"
          class="link"
          onClick={() =>
            void act(async () => {
              await api.signOut();
              onSignedOut();
            })
          }
        >
          {text.signOut}
        </button>
      </div>
      {notice && (
        <p class="panel warn" role="alert">
          {notice}
        </p>
      )}
      <PlanSection me={me} api={api} go={go} act={act} reload={reload} />
      <CampaignsSection me={me} api={api} act={act} reload={reload} />
      <ServersSection me={me} api={api} go={go} act={act} />
      <DeleteSection api={api} act={act} onDeleted={onDeleted} />
    </div>
  );
}

interface SectionProps {
  me: Me;
  api: AccountApi;
  act: Act;
}

function PlanSection({
  me,
  api,
  go,
  act,
  reload,
}: SectionProps & { go: (url: string) => void; reload: () => Promise<void> }) {
  const plan = me.plan;
  const choosePaid = (id: PlanId): void =>
    void act(async () => go(await api.checkoutUrl(id)));
  const portal = (): void => void act(async () => go(await api.billingPortalUrl()));

  return (
    <section aria-labelledby="plan-heading" class="panel">
      <h2 id="plan-heading">{text.planHeading}</h2>
      {plan === null ? (
        <>
          <p>{text.noPlan}</p>
          <button
            type="button"
            class="button"
            onClick={() =>
              void act(async () => {
                await api.startTryIt();
                await reload();
              })
            }
          >
            {text.startTryIt}
          </button>
          <p class="muted small">{text.startTryItNote}</p>
          <PlanChoices title={text.orPick} onChoose={choosePaid} />
        </>
      ) : (
        <>
          <p class="plan-name">{planName(plan.id)}</p>
          {plan.status === "grace" && plan.graceEndsOn && (
            <div class="warn-box" role="alert">
              <p>{text.grace(plan.graceEndsOn)}</p>
              <button type="button" class="button" onClick={portal}>
                {text.fixPayment}
              </button>
            </div>
          )}
          {plan.status === "lapsed" ? (
            <>
              <p role="alert">{text.lapsed}</p>
              <PlanChoices title={text.pickPlan} onChoose={choosePaid} />
            </>
          ) : (
            <>
              <p class="hours-line">{hoursUsedLine(plan.hoursUsed, plan.hoursCap)}</p>
              <progress
                max={plan.hoursCap}
                value={Math.min(plan.hoursUsed, plan.hoursCap)}
                aria-label={text.hoursBarLabel}
              />
              <p class="muted small">
                {hoursLeftLine(plan.hoursUsed, plan.hoursCap, plan.renewsOn)}
              </p>
              {plan.id === "try-it" ? (
                <PlanChoices title={text.pickPlan} onChoose={choosePaid} />
              ) : (
                <button type="button" class="button secondary" onClick={portal}>
                  {text.changePlan}
                </button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function PlanChoices({ title, onChoose }: { title: string; onChoose: (id: PlanId) => void }) {
  return (
    <div class="choices">
      <p>{title}</p>
      <ul>
        {paidPlans.map((p) => (
          <li key={p.id}>
            <span>
              <strong>{p.name}</strong> · {formatPrice(p.priceCents ?? 0)} a month ·{" "}
              {p.hoursLine}
            </span>
            <button type="button" class="button secondary" onClick={() => onChoose(p.id)}>
              {text.choose(p.id)}
            </button>
          </li>
        ))}
      </ul>
      <a href="/pricing">{text.seePrices}</a>
    </div>
  );
}

function CampaignsSection({ me, api, act, reload }: SectionProps & { reload: () => Promise<void> }) {
  const [done, setDone] = useState<string | null>(null);
  return (
    <section aria-labelledby="campaigns-heading" class="panel">
      <h2 id="campaigns-heading">{text.campaignsHeading}</h2>
      {done && (
        <p class="ok" role="status">
          {done}
        </p>
      )}
      {me.campaigns.length === 0 ? (
        <p>{text.noCampaigns}</p>
      ) : (
        <ul class="campaigns">
          {me.campaigns.map((c) => (
            <CampaignRow
              key={c.id}
              campaign={c}
              api={api}
              act={act}
              onHandedOver={async (person) => {
                setDone(text.handOverDone(c.name, person.name));
                await reload();
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function CampaignRow({
  campaign,
  api,
  act,
  onHandedOver,
}: {
  campaign: Campaign;
  api: AccountApi;
  act: Act;
  onHandedOver: (person: Person) => Promise<void>;
}) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const formId = `handover-${campaign.id}`;

  return (
    <li data-campaign={campaign.id}>
      <p class="campaign-name">{campaign.name}</p>
      <p class="muted small">
        {campaign.serverName} ·{" "}
        {campaign.lastPlayedAt ? text.lastPlayed(campaign.lastPlayedAt) : text.notPlayed}
      </p>
      <p class="tags">
        <span class={`tag ${campaign.status}`}>
          {campaign.status === "active" ? text.active : text.paused}
        </span>
        <span class="tag">{campaign.role === "owner" ? text.youRunIt : text.youHelp}</span>
      </p>
      {campaign.role === "owner" && people === null && (
        <button
          type="button"
          class="button secondary"
          onClick={() =>
            void act(async () => {
              setPeople(await api.handoverCandidates(campaign.id));
            })
          }
        >
          {text.handOver}
        </button>
      )}
      {people !== null && (
        <form
          id={formId}
          class="handover"
          onSubmit={(event) => {
            event.preventDefault();
            const person = people.find((p) => p.id === chosen);
            if (!person) return;
            void act(
              async () => {
                await api.handover(campaign.id, person.id);
                setPeople(null);
                await onHandedOver(person);
              },
              (error) => (error.kind === "no-free-slot" ? text.noFreeSlot : null),
            );
          }}
        >
          <fieldset>
            <legend>{text.handOverQuestion(campaign.name)}</legend>
            {people.length === 0 ? (
              <p>{text.handOverNobody}</p>
            ) : (
              <>
                <p class="muted small">{text.handOverNote}</p>
                {people.map((p) => (
                  <label key={p.id} class="choice">
                    <input
                      type="radio"
                      name={`${formId}-person`}
                      value={p.id}
                      checked={chosen === p.id}
                      onChange={() => setChosen(p.id)}
                    />
                    {p.name}
                  </label>
                ))}
              </>
            )}
          </fieldset>
          <div class="row">
            {people.length > 0 && (
              <button type="submit" class="button" disabled={chosen === null}>
                {text.handOverConfirm}
              </button>
            )}
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setPeople(null);
                setChosen(null);
              }}
            >
              {text.cancel}
            </button>
          </div>
        </form>
      )}
    </li>
  );
}

function ServersSection({ me, api, go, act }: SectionProps & { go: (url: string) => void }) {
  return (
    <section aria-labelledby="servers-heading" class="panel">
      <h2 id="servers-heading">{text.serversHeading}</h2>
      {me.servers.length === 0 ? (
        <p>{text.noServers}</p>
      ) : (
        <>
          <p class="muted small">{text.serversNote}</p>
          <ul class="servers">
            {me.servers.map((s) => (
              <li key={s.id} data-server={s.id}>
                <span>{s.name}</span>
                {s.hasDmbot ? (
                  <span class="muted">{text.alreadyThere}</span>
                ) : (
                  <button
                    type="button"
                    class="button secondary"
                    onClick={() => void act(async () => go(await api.installUrl(s.id)))}
                  >
                    {text.addTo}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function DeleteSection({
  api,
  act,
  onDeleted,
}: {
  api: AccountApi;
  act: Act;
  onDeleted: () => void;
}) {
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [token, setToken] = useState<string | null>(null);

  return (
    <section aria-labelledby="delete-heading" class="panel danger">
      <h2 id="delete-heading">{text.deleteHeading}</h2>
      {step === 0 && (
        <button type="button" class="button secondary" onClick={() => setStep(1)}>
          {text.deleteStart}
        </button>
      )}
      {step === 1 && (
        <>
          <p>{text.deleteWarning}</p>
          <div class="row">
            <button
              type="button"
              class="button danger"
              onClick={() =>
                void act(async () => {
                  setToken(await api.requestDelete());
                  setStep(2);
                })
              }
            >
              {text.deleteNext}
            </button>
            <button type="button" class="button secondary" onClick={() => setStep(0)}>
              {text.keep}
            </button>
          </div>
        </>
      )}
      {step === 2 && token !== null && (
        <>
          <p role="alert">
            <strong>{text.deleteSure}</strong>
          </p>
          <div class="row">
            <button
              type="button"
              class="button danger"
              onClick={() =>
                void act(async () => {
                  await api.confirmDelete(token);
                  onDeleted();
                })
              }
            >
              {text.deleteConfirm}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                setStep(0);
                setToken(null);
              }}
            >
              {text.cancel}
            </button>
          </div>
        </>
      )}
    </section>
  );
}


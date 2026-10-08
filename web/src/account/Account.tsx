/** @jsxImportSource preact */
// The signed-in area (#434): one island on /account. All data comes from the web API in
// core (#435); this file only shows it and sends the user's choices back.
import type { ComponentChildren, JSX } from "preact";
import { createContext } from "preact";
import { useCallback, useContext, useEffect, useRef, useState } from "preact/hooks";

import { hoursLeftLine, hoursUsedLine, planName, text } from "../content/account";
import { formatPrice, plans as paidPlans, type PlanId } from "../content/pricing";
import {
  ApiError,
  type AccountApi,
  type Campaign,
  type Me,
  type Offer,
  type Person,
} from "./api";
import { isSafeRedirect } from "./redirect";

type State =
  | { kind: "loading" }
  | { kind: "down" }
  | { kind: "signed-out"; notice: string | null }
  | { kind: "signed-in"; me: Me }
  | { kind: "deleted" };

interface Props {
  api: AccountApi;
  /** Where to send the browser (checkout, billing, Discord). Swappable for tests. */
  go?: (url: string) => void;
  /** The page's query string, for the sign-in result (?signin=failed). */
  search?: string;
}

const defaultGo = (url: string): void => {
  window.location.assign(url);
};

interface Shared {
  api: AccountApi;
  /** Send the browser to Discord or the payment company; anything else is refused. */
  go: (url: string) => void;
  /** Fetch the account again after a change. A failure keeps what's on screen. */
  refresh: () => Promise<void>;
  /** The session ended (or must be renewed): back to sign-in, with a message. */
  signedOut: (notice?: string) => void;
}

const SharedContext = createContext<Shared | null>(null);

function useShared(): Shared {
  const shared = useContext(SharedContext);
  if (!shared) throw new Error("Account sections need the Account component around them");
  return shared;
}

/**
 * One action at a time per section: the button shows "One moment…" while it runs, a second
 * tap does nothing, and a failure shows its message inside that section, where the user is.
 */
function useAction() {
  const { signedOut } = useShared();
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (action: () => Promise<void>, explain?: (error: ApiError) => string | null) => {
      if (running.current) return;
      running.current = true;
      setBusy(true);
      setNotice(null);
      try {
        await action();
      } catch (error) {
        const apiError = error instanceof ApiError ? error : new ApiError("server");
        if (apiError.kind === "signed-out") {
          signedOut();
          return;
        }
        if (apiError.kind === "sign-in-again") {
          signedOut(text.signInAgain);
          return;
        }
        setNotice(explain?.(apiError) ?? text.errors[apiError.kind] ?? text.actionFailed);
      } finally {
        running.current = false;
        setBusy(false);
      }
    },
    [signedOut],
  );

  const clear = useCallback(() => setNotice(null), []);
  return { busy, notice, run, clear };
}

/** For campaign actions: "not allowed" there means only the campaign's DM can. */
function campaignRefusal(error: ApiError): string | null {
  return error.kind === "not-allowed" ? text.notTheDm : null;
}

function Notice({ message }: { message: string | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [message]);
  if (!message) return null;
  return (
    <p ref={ref} class="warn" role="alert">
      {message}
    </p>
  );
}

function ActionButton({
  busy,
  onClick,
  kind = "primary",
  children,
}: {
  busy: boolean;
  onClick: () => void;
  kind?: "primary" | "secondary" | "danger";
  children: ComponentChildren;
}) {
  const classes = kind === "primary" ? "button" : `button ${kind}`;
  return (
    <button type="button" class={classes} disabled={busy} aria-busy={busy} onClick={onClick}>
      {busy ? text.busy : children}
    </button>
  );
}

export default function Account({ api, go = defaultGo, search = "" }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    try {
      const me = await api.me();
      const params = new URLSearchParams(search);
      if (me && params.get("install") === "sign_in_again") {
        setState({ kind: "signed-out", notice: text.signInAgain });
        return;
      }
      if (!me && params.get("install") === "signed_out") {
        setState({ kind: "signed-out", notice: text.installSignedOut });
        return;
      }
      const failed = params.get("signin") === "failed";
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

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setState(me ? { kind: "signed-in", me } : { kind: "signed-out", notice: null });
    } catch {
      // The change worked; only the refresh failed. Keep showing what we have.
    }
  }, [api]);

  const signedOut = useCallback((notice: string = text.signedOutNow) => {
    setState({ kind: "signed-out", notice });
  }, []);

  const safeGo = useCallback(
    (url: string) => {
      if (!isSafeRedirect(url)) throw new ApiError("server");
      go(url);
    },
    [go],
  );

  const shared: Shared = { api, go: safeGo, refresh, signedOut };

  let body: JSX.Element;
  switch (state.kind) {
    case "loading":
      body = (
        <p class="muted" role="status">
          {text.loading}
        </p>
      );
      break;
    case "down":
      body = (
        <div class="panel" role="alert">
          <p>{text.down}</p>
          <button type="button" class="button" onClick={() => void load()}>
            {text.tryAgain}
          </button>
        </div>
      );
      break;
    case "signed-out":
      body = <SignedOut notice={state.notice} />;
      break;
    case "deleted":
      body = (
        <p class="panel" role="status">
          {text.deleted}
        </p>
      );
      break;
    case "signed-in":
      body = (
        <SignedIn
          installResult={installResult(search)}
          me={state.me}
          onDeleted={() => setState({ kind: "deleted" })}
          onSignedOutByChoice={() => setState({ kind: "signed-out", notice: null })}
        />
      );
      break;
  }
  return <SharedContext.Provider value={shared}>{body}</SharedContext.Provider>;
}

function SignedOut({ notice }: { notice: string | null }) {
  const { api } = useShared();
  const signIn = api.signInUrl();
  return (
    <div class="stack">
      {notice && (
        <p class="warn" role="alert">
          {notice}
        </p>
      )}
      <p class="lead">{text.signInLead}</p>
      <div class="panel">
        <a class="button" href={signIn}>
          {text.startTryItFree}
        </a>
        <p class="muted small">{text.startTryItSignIn}</p>
        <PlanList
          choose={(id) => (
            <a class="button secondary" href={signIn}>
              {text.choose(id)}
            </a>
          )}
        />
      </div>
      <div class="panel">
        <p>{text.haveAccount}</p>
        <a class="button secondary" href={signIn}>
          {text.signIn}
        </a>
        <p class="muted small">{text.signInNote}</p>
      </div>
    </div>
  );
}

function ForgetInstallResult() {
  useEffect(forgetInstallResult, []);
  return null;
}

/** The message for coming back from adding DMbot (?install=done etc.), or null. */
function installResult(search: string): string | null {
  const result = new URLSearchParams(search).get("install");
  // Signed in again since (another tab, a reload): nothing true left to say about it.
  if (result === null || result === "signed_out") return null;
  return text.install[result] ?? text.install["failed"] ?? null;
}

/** Show the ?install= result once: take it out of the address so a reload doesn't repeat it. */
function forgetInstallResult(): void {
  if (typeof window === "undefined" || !window.location.search.includes("install=")) return;
  const url = new URL(window.location.href);
  url.searchParams.delete("install");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

function SignedIn({
  me,
  installResult: installMessage,
  onDeleted,
  onSignedOutByChoice,
}: {
  me: Me;
  installResult: string | null;
  onDeleted: () => void;
  onSignedOutByChoice: () => void;
}) {
  const { api } = useShared();
  const { busy, notice, run } = useAction();
  // Kept here, not in the offers section: answering the last offer removes that section.
  const [offerNews, setOfferNews] = useState<{ text: string; ok: boolean } | null>(null);
  return (
    <div class="stack">
      <div class="hello">
        <p class="lead">{text.greeting(me.user.name)}</p>
        <button
          type="button"
          class="link"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.signOut();
              onSignedOutByChoice();
            })
          }
        >
          {text.signOut}
        </button>
      </div>
      <Notice message={notice} />
      {installMessage && <ForgetInstallResult />}
      {installMessage && (
        <p class={installMessage === text.install["done"] ? "ok" : "warn"} role="status">
          {installMessage}
        </p>
      )}
      {offerNews && (
        <p class={offerNews.ok ? "ok" : "warn"} role="status">
          {offerNews.text}
        </p>
      )}
      {me.offers.incoming.length > 0 && (
        <OffersSection offers={me.offers.incoming} onNews={setOfferNews} />
      )}
      <PlanSection me={me} />
      <CampaignsSection me={me} />
      <ServersSection me={me} />
      <DeleteSection
        paidPlan={me.plan !== null && me.plan.id !== "try-it" && me.plan.status !== "lapsed"}
        onDeleted={onDeleted}
      />
    </div>
  );
}

function PlanList({ choose }: { choose: (id: PlanId) => JSX.Element }) {
  return (
    <div class="choices">
      <ul>
        {paidPlans.map((p) => (
          <li key={p.id}>
            <span>
              <strong>{p.name}</strong> · {formatPrice(p.priceCents ?? 0)} a month ·{" "}
              {p.hoursLine}
            </span>
            {choose(p.id)}
          </li>
        ))}
      </ul>
      <a href="/pricing">{text.seePrices}</a>
    </div>
  );
}

function PlanSection({ me }: { me: Me }) {
  const { api, go, refresh } = useShared();
  const { busy, notice, run } = useAction();
  const plan = me.plan;
  const portal = (): void =>
    void run(
      async () => go(await api.billingPortalUrl()),
      (error) => {
        // The page is out of date (the plan stopped, or was never paid through the
        // company): show what's true now, with the plans to pick from.
        if (error.kind === "no-paid-plan") void refresh();
        return null;
      },
    );
  const choices = (title: string) => (
    <>
      <p>{title}</p>
      <PlanList
        choose={(id) => (
          <ActionButton
            busy={busy}
            kind="secondary"
            onClick={() => void run(async () => go(await api.checkoutUrl(id)))}
          >
            {text.choose(id)}
          </ActionButton>
        )}
      />
    </>
  );

  return (
    <section aria-labelledby="plan-heading" class="panel" id="plan">
      <h2 id="plan-heading">{text.planHeading}</h2>
      <Notice message={notice} />
      {plan === null ? (
        <>
          <p>{text.noPlan}</p>
          <ActionButton
            busy={busy}
            onClick={() =>
              void run(async () => {
                await api.startTryIt();
                await refresh();
              })
            }
          >
            {text.startTryIt}
          </ActionButton>
          <p class="muted small">{text.startTryItNote}</p>
          {choices(text.orPick)}
        </>
      ) : (
        <>
          <p class="plan-name">{planName(plan.id)}</p>
          {plan.status === "grace" && (
            <div class="warn-box" role="alert">
              <p>{text.grace(plan.graceEndsOn)}</p>
              <ActionButton busy={busy} onClick={portal}>
                {text.fixPayment}
              </ActionButton>
            </div>
          )}
          {plan.status === "lapsed" ? (
            <>
              <p role="alert">{text.lapsed}</p>
              {choices(text.pickPlan)}
            </>
          ) : (
            <>
              <p class="hours-line">{hoursUsedLine(plan.hoursUsed, plan.hoursCap)}</p>
              {plan.hoursCap > 0 && (
                <progress
                  max={plan.hoursCap}
                  value={Math.min(Math.max(plan.hoursUsed, 0), plan.hoursCap)}
                  aria-label={text.hoursBarLabel}
                />
              )}
              <p class="muted small">
                {hoursLeftLine(plan.hoursUsed, plan.hoursCap, plan.renewsOn, plan.id)}
              </p>
              {plan.id === "try-it" ? (
                choices(text.pickPlan)
              ) : (
                <ActionButton busy={busy} kind="secondary" onClick={portal}>
                  {text.changePlan}
                </ActionButton>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

/** Campaigns someone wants to hand you (#614). Accept re-checks your free slot on the server. */
function OffersSection({
  offers,
  onNews,
}: {
  offers: Offer[];
  onNews: (news: { text: string; ok: boolean }) => void;
}) {
  return (
    <section aria-labelledby="offers-heading" class="panel" id="offers">
      <h2 id="offers-heading">{text.offersHeading}</h2>
      <ul class="campaigns">
        {offers.map((o) => (
          <OfferRow key={o.id} offer={o} onNews={onNews} />
        ))}
      </ul>
    </section>
  );
}

function OfferRow({
  offer,
  onNews,
}: {
  offer: Offer;
  onNews: (news: { text: string; ok: boolean }) => void;
}) {
  const { api, refresh } = useShared();
  const { busy, notice, run } = useAction();
  const [noSlot, setNoSlot] = useState(false);
  const answer = (action: () => Promise<void>, message: string): void =>
    void run(
      async () => {
        setNoSlot(false);
        await action();
        onNews({ text: message, ok: true });
        await refresh();
      },
      (error) => {
        if (error.kind === "no-free-slot") {
          setNoSlot(true);
          return text.acceptNoSlot;
        }
        if (error.kind === "offer-gone") {
          // The row goes with the refresh, so the news is shown above the sections.
          onNews({ text: text.errors["offer-gone"] ?? text.actionFailed, ok: false });
          void refresh();
        }
        return null;
      },
    );
  return (
    <li data-offer={offer.id}>
      <p>{text.offerIncoming(offer.personName, offer.campaignName, offer.serverName)}</p>
      <p class="muted small">{text.offerExpires(offer.expiresAt)}</p>
      <Notice message={notice} />
      {noSlot && <a href="#plan">{text.seeMyPlan}</a>}
      <div class="row">
        <ActionButton
          busy={busy}
          onClick={() => answer(() => api.acceptOffer(offer.id), text.accepted(offer.campaignName))}
        >
          {text.accept}
        </ActionButton>
        <ActionButton
          busy={busy}
          kind="secondary"
          onClick={() => answer(() => api.declineOffer(offer.id), text.declined(offer.personName))}
        >
          {text.decline}
        </ActionButton>
      </div>
    </li>
  );
}

function CampaignsSection({ me }: { me: Me }) {
  const { refresh } = useShared();
  const [done, setDone] = useState<string | null>(null);
  return (
    <section aria-labelledby="campaigns-heading" class="panel" id="campaigns">
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
              offer={me.offers.outgoing.find((o) => o.campaignId === c.id) ?? null}
              onWithdrawn={async () => {
                setDone(text.withdrawn);
                await refresh();
              }}
              onHandedOver={async (person) => {
                setDone(text.handOverDone(person.name));
                await refresh();
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
  offer,
  onWithdrawn,
  onHandedOver,
}: {
  campaign: Campaign;
  /** A hand-over of this campaign you offered and nobody has answered yet. */
  offer: Offer | null;
  onWithdrawn: () => Promise<void>;
  onHandedOver: (person: Person) => Promise<void>;
}) {
  const { api, refresh } = useShared();
  const { busy, notice, run } = useAction();
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
      <Notice message={notice} />
      {offer && (
        <>
          <p class="small">{text.offerOutgoing(offer.personName, offer.expiresAt)}</p>
          <div class="row">
            <ActionButton
              busy={busy}
              kind="secondary"
              onClick={() =>
                void run(
                  async () => {
                    await api.withdrawOffer(offer.id);
                    await onWithdrawn();
                  },
                  (error) => {
                    if (error.kind !== "offer-gone") return campaignRefusal(error);
                    void refresh(); // it was answered or ended: show what's true now
                    return null;
                  },
                )
              }
            >
              {text.withdraw}
            </ActionButton>
          </div>
        </>
      )}
      {campaign.role === "owner" && !offer && people === null && (
        <ActionButton
          busy={busy}
          kind="secondary"
          onClick={() =>
            void run(async () => {
              setPeople(await api.handoverCandidates(campaign.id));
            }, campaignRefusal)
          }
        >
          {text.handOver}
        </ActionButton>
      )}
      {people !== null && (
        <form
          id={formId}
          class="handover"
          onSubmit={(event) => {
            event.preventDefault();
            const person = people.find((p) => p.id === chosen);
            if (!person) return;
            void run(
              async () => {
                await api.handover(campaign.id, person.id);
                setPeople(null);
                await onHandedOver(person);
              },
              // Never a plan message: the owner must not learn whether someone pays (#437).
              // The API no longer refuses an offer for that; an old one gets the plain line.
              (error) =>
                error.kind === "no-free-slot"
                  ? (text.errors["not-allowed"] ?? null)
                  : campaignRefusal(error),
            );
          }}
        >
          <fieldset disabled={busy}>
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
              <button
                type="submit"
                class="button"
                disabled={chosen === null || busy}
                aria-busy={busy}
              >
                {busy ? text.busy : text.handOverConfirm}
              </button>
            )}
            <button
              type="button"
              class="button secondary"
              disabled={busy}
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

function ServersSection({ me }: { me: Me }) {
  const { api } = useShared();
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
              <ServerRow key={s.id} server={s} api={api} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** One server: its own busy state and message, so tapping one never ties up the rest. */
function ServerRow({ server: s, api }: { server: Me["servers"][number]; api: AccountApi }) {
  const { refresh } = useShared();
  const { busy, notice, run } = useAction();
  return (
    <li data-server={s.id}>
      <span>{s.name}</span>
      {!s.hasDmbot ? (
        // A plain link: the API checks, then sends the browser on to Discord.
        <a class="button secondary" href={api.installUrl(s.id)}>
          {text.addTo}
        </a>
      ) : s.canLink ? (
        <ActionButton
          busy={busy}
          kind="secondary"
          onClick={() =>
            void run(async () => {
              await api.linkServer(s.id);
              await refresh();
            })
          }
        >
          {text.linkServer}
        </ActionButton>
      ) : (
        <span class="muted">{s.installedByYou ? text.youAddedIt : text.alreadyThere}</span>
      )}
      {s.hasDmbot && s.canLink && <p class="muted small row-note">{text.linkNote}</p>}
      <Notice message={notice} />
    </li>
  );
}

function DeleteSection({ paidPlan, onDeleted }: { paidPlan: boolean; onDeleted: () => void }) {
  const { api } = useShared();
  const { busy, notice, run, clear } = useAction();
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [token, setToken] = useState<string | null>(null);
  const reset = (): void => {
    setStep(0);
    setToken(null);
  };

  return (
    <section aria-labelledby="delete-heading" class="panel danger">
      <h2 id="delete-heading">{text.deleteHeading}</h2>
      <Notice message={notice} />
      {step === 0 && (
        <button
          type="button"
          class="button secondary"
          onClick={() => {
            clear(); // a "took too long" from last time is about the old attempt
            setStep(1);
          }}
        >
          {text.deleteStart}
        </button>
      )}
      {step === 1 && (
        <>
          {text.deleteWarning.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <a href="#campaigns">{text.deleteHandOverLink}</a>
          {paidPlan && (
            <>
              <p>{text.deletePlanStops}</p>
              <a href="/legal/refunds#stopping">{text.deleteRefundsLink}</a>
            </>
          )}
          <div class="row">
            <ActionButton
              busy={busy}
              kind="danger"
              onClick={() =>
                void run(async () => {
                  setToken(await api.requestDelete());
                  setStep(2);
                })
              }
            >
              {text.deleteNext}
            </ActionButton>
            <button type="button" class="button secondary" disabled={busy} onClick={reset}>
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
          {/* "Keep" comes first, where step 1's red button was, so a quick double tap
              can't delete the account. */}
          <div class="row">
            <button type="button" class="button secondary" disabled={busy} onClick={reset}>
              {text.keep}
            </button>
            <ActionButton
              busy={busy}
              kind="danger"
              onClick={() =>
                void run(async () => {
                  try {
                    await api.confirmDelete(token);
                  } catch (error) {
                    reset(); // back to step 0 ("Start deleting"): a retry gets a new confirmation
                    throw error;
                  }
                  onDeleted();
                })
              }
            >
              {text.deleteConfirm}
            </ActionButton>
          </div>
        </>
      )}
    </section>
  );
}

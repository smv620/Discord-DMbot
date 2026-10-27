/** @jsxImportSource preact */
// The signed-in area (#434): one island on /account. All data comes from the web API in
// core (#435); this file only shows it and sends the user's choices back.
import type { JSX } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { recapCard } from "../content/account-cards";
import { sharingCard } from "../content/sharing";
import { RECAPS_LIVE } from "../content/recap-words";
import { RECAP_AUDIO_LIVE } from "../content/recap-words";
import { accountWords as audioWords, price as audioPrice } from "../content/recap-audio";
import { CAMPAIGN_DELETE_LIVE, campaignDeleteWords } from "../content/campaign-delete";
import { HANDOVER_LIVE, handoverOwnerWords, handoverWords } from "../content/handover-words";
import { handoverToken } from "./handover-token";
import { DISCORD_ACCOUNTS_LIVE, discordAccountsWords } from "../content/discord-accounts";
import { keepFocus } from "./focus";
import { hoursLeftLine, hoursUsedLine, planName, shortDateTime, text } from "../content/account";
import { portalLoginUrl, portalWords } from "../content/billing-login";
import { formatPrice, plans as paidPlans, type PlanId } from "../content/pricing";
import {
  ApiError,
  type AccountApi,
  type Campaign,
  type Character,
  type DiscordAccount,
  type Me,
  type MyPlan,
  type Offer,
  type Person,
  type RecordingEntry,
} from "./api";
import CampaignDelete from "./CampaignDelete";
import { campaignHref } from "./campaignLinks";
import { foldCampaigns } from "./campaign-fold";
import CampaignFacts from "./CampaignFacts";
import { isSafeRedirect } from "./redirect";
import LazyPanel from "./LazyPanel";
import { ActionButton, Notice, SharedContext, emailConfirmToken, forgetEmailLink, rememberConfirm, useAction, useShared, type Shared } from "./account-shared";

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
  /** The page's `#...`: an emailed sign-in link puts its one-use code there (#1231), so no
   * server or log ever sees it. */
  hash?: string;
  /** Cloudflare Turnstile's site key for the Support form, or "" to skip the check (#1340). */
  siteKey?: string;
}

/** The code in `#email-link=...`, or null. */
function emailLinkToken(hash: string): string | null {
  const found = /^#email-link=([\w-]{1,200})$/.exec(hash);
  return found ? (found[1] ?? null) : null;
}

const defaultGo = (url: string): void => {
  window.location.assign(url);
};

/** For campaign actions: "not allowed" there means only the campaign's DM can. */
function campaignRefusal(error: ApiError): string | null {
  return error.kind === "not-allowed" ? text.notTheDm : null;
}

export default function Account({
  api,
  go = defaultGo,
  search = "",
  hash = "",
  siteKey = "",
}: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  // An emailed link is tried once, even if the page loads again ("Try again").
  const linkTried = useRef(false);

  const load = useCallback(async () => {
    try {
      const link = emailLinkToken(hash);
      let linkNotice: string | null = null;
      if (link && api.emailSignInUse && !linkTried.current) {
        linkTried.current = true;
        forgetEmailLink();
        try {
          await api.emailSignInUse(link);
        } catch (error) {
          // A wrong, used or ended link is said in words. Anything else (the API down) falls
          // through to the page's own "down" answer from api.me() below.
          // A refusal for too many tries did not spend the link, so it says to wait, not that the
          // link was used.
          if (error instanceof ApiError && error.kind === "bad-link") linkNotice = text.emailLinkBad;
          else if (error instanceof ApiError && error.kind === "slow-down") {
            linkNotice = text.emailLinkWait;
          } else throw error;
        }
      }
      const me = await api.me();
      if (!me && linkNotice) {
        setState({ kind: "signed-out", notice: linkNotice });
        return;
      }
      // A change-of-email link only works for the account that asked, and its code stays in the
      // address while the person signs in, so say so instead of showing a plain sign-in page.
      const waiting = emailConfirmToken(hash);
      if (!me && waiting) {
        rememberConfirm(waiting);
        setState({ kind: "signed-out", notice: text.emailConfirmSignIn });
        return;
      }
      const params = new URLSearchParams(search);
      if (me && params.get("install") === "sign_in_again") {
        setState({ kind: "signed-out", notice: text.signInAgain });
        return;
      }
      if (!me && params.get("install") === "signed_out") {
        setState({ kind: "signed-out", notice: text.installSignedOut });
        return;
      }
      const why = params.get("signin");
      const notice =
        why === "failed"
          ? text.signInFailed
          : why === "slow"
            ? text.tooManyTries
            : why === "moved"
              ? text.signInMoved
              : null;
      setState(me ? { kind: "signed-in", me } : { kind: "signed-out", notice });
    } catch {
      setState({ kind: "down" });
    }
  }, [api, search, hash]);

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

  const access = state.kind === "signed-in" ? state.me.access : undefined;
  const noLimits = access?.kind === "free" && access.unlimited === true;
  const shared: Shared = { api, go: safeGo, refresh, signedOut, noLimits, siteKey };

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
          discordNews={DISCORD_ACCOUNTS_LIVE ? discordResult(search) : null}
          hash={hash}
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
  // Sign-in comes first (#2003): on a phone this page used to open on the plans, and the plain
  // Sign in button sat below all of them, so a returning DM thought they could only buy.
  return (
    <div class="stack">
      <section class="panel" aria-labelledby="signed-out-heading">
        {notice && (
          <p class="warn" role="alert">
            {notice}
          </p>
        )}
        <h2 id="signed-out-heading">{text.signedOutHeading}</h2>
        <p>{text.signedOutLead}</p>
        <a class="button" href={signIn}>
          {text.signIn}
        </a>
        <p class="muted small">{text.signInNote}</p>
        <EmailSignIn />
      </section>
      <section class="panel" aria-labelledby="new-heading">
        <h2 id="new-heading">{text.newHeading}</h2>
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
      </section>
    </div>
  );
}

/** Sign in without Discord (#1231): type an email, get a one-use link. The answer is always the
 * same words, so the page can't be used to find out whether an address has an account. */
function EmailSignIn() {
  const { api } = useShared();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<{ text: string; ok: boolean } | null>(null);
  const request = api.emailSignInRequest;
  if (!request) return null; // an API older than #1231
  const send = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (busy) return;
    const address = email.trim();
    if (address === "") {
      setAnswer({ text: text.emailMissing, ok: false });
      return;
    }
    setBusy(true);
    setAnswer(null);
    try {
      await request(address);
      setAnswer({ text: text.emailAnswer, ok: true });
    } catch (error) {
      const slow = error instanceof ApiError && error.kind === "slow-down";
      setAnswer({ text: slow ? text.emailWait : text.actionFailed, ok: false });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form class="email-sign-in" aria-labelledby="email-heading" onSubmit={(e) => void send(e)}>
      <h3 id="email-heading">{text.emailHeading}</h3>
      <label for="email-address">{text.emailLabel}</label>
      <input
        id="email-address"
        type="email"
        name="email"
        autocomplete="email"
        placeholder="your@email.here"
        value={email}
        onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
      />
      <button type="submit" class="button secondary" disabled={busy} aria-busy={busy}>
        {busy ? text.emailSending : text.emailButton}
      </button>
      {answer && (
        <p class={answer.ok ? "ok" : "warn"} role={answer.ok ? "status" : "alert"}>
          {answer.text}
        </p>
      )}
      <p class="muted small">{text.emailNote}</p>
    </form>
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
  hash,
  installResult: installMessage,
  discordNews,
  onDeleted,
  onSignedOutByChoice,
}: {
  me: Me;
  /** The page's `#...`, for the change-of-email link (#2349). */
  hash: string;
  installResult: string | null;
  /** What coming back from adding a Discord account says (?discord=...), or null (#2534). */
  discordNews: string | null;
  onDeleted: () => void;
  onSignedOutByChoice: () => void;
}) {
  const { api, siteKey } = useShared();
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
        <button
          type="button"
          class="link"
          disabled={busy}
          onClick={() =>
            void run(
              async () => {
                await api.signOutEverywhere();
                onSignedOutByChoice();
              },
              (error) => (error.kind === "slow-down" ? text.signOutEverywhereWait : null),
            )
          }
        >
          {text.signOutEverywhere}
        </button>
        <p class="muted small">{text.signOutEverywhereNote}</p>
      </div>
      <Notice message={notice} />
      {HANDOVER_LIVE && handoverToken("") !== null && (
        // An offer link was opened before signing in: its code was kept, so the way back is here.
        <p class="note" role="status">
          {handoverWords.waitingOnAccount} <a href="/handover">{handoverWords.waitingLink}</a>
        </p>
      )}
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
        <OffersSection
          offers={me.offers.incoming}
          free={me.access?.kind === "free"}
          onNews={setOfferNews}
        />
      )}
      <CampaignsSection me={me} />
      <CharactersSection />
      <SharedRulesSection me={me} />
      <PlanSection me={me} />
      <h2 id="my-account-heading">{text.myAccountHeading}</h2>
      {me.dmbotId !== undefined && <IdSection me={me} />}
      {DISCORD_ACCOUNTS_LIVE && me.discordAccounts && api.addDiscordUrl && api.removeDiscordAccount && (
        <DiscordAccountsSection accounts={me.discordAccounts} resultText={discordNews} />
      )}
      {api.myEmail && (
        <LazyPanel load={() => import("./AccountEmail")} what="the email settings" props={{ hash }} />
      )}
      <ServersSection me={me} />
      {me.canMove === true && api.getMoveCode && <MoveSection />}
      <LazyPanel
        load={() => import("./AccountTalk")}
        what="the places to write to the team"
        props={{ api, siteKey, me }} />
      <RecordingSection entries={me.recording ?? []} />
      <ExportSection />
      <DeleteSection
        paidPlan={me.plan !== null && me.plan.id !== "try-it" && me.plan.status !== "lapsed"}
        free={me.access?.kind === "free"}
        stillPaying={me.access?.stillPaying === true}
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

/** What a paid plan is doing. An API older than #2165 gives no state: a cancel date means ending. */
function planState(plan: MyPlan): NonNullable<MyPlan["state"]> {
  if (plan.state) return plan.state;
  if (plan.status === "lapsed") return "expired";
  return plan.endsOn ? "ending" : "active";
}

/** The plan line (#2165): the name, and what the plan is doing, in the site's date words. Try It
 * and a payment still being fixed keep the plain name. */
function planLine(plan: MyPlan): string {
  const name = planName(plan.id);
  if (plan.id === "try-it" || plan.status === "grace") return name;
  const state = planState(plan);
  if (state === "expired")
    return plan.endedOn ? text.planExpired(name, plan.endedOn) : text.planExpiredNoDate(name);
  if (state === "ending" && plan.endsOn) return text.planEndsLine(name, plan.endsOn);
  if (state === "changing" && plan.nextPlan && plan.nextPlanOn)
    return text.planChanging(name, planName(plan.nextPlan), plan.nextPlanOn);
  return plan.renewsOn ? text.planRenews(name, plan.renewsOn) : name;
}

function PlanSection({ me }: { me: Me }) {
  const { api, go, refresh } = useShared();
  const { busy, notice, run } = useAction();
  const plan = me.plan;
  const extraHours = (): void =>
    void run(
      async () => go(await api.extraHoursUrl()),
      (error) => {
        // The plan changed since the page opened (it stopped, or free access began):
        // show what's true now.
        if (error.kind === "no-paid-plan" || error.kind === "free-access") void refresh();
        return null;
      },
    );
  const recapAudio = (): void =>
    void run(
      async () => {
        if (!api.recapAudioUrl) throw new ApiError("server");
        go(await api.recapAudioUrl());
      },
      (error) => {
        // The plan changed since the page opened (it came with audio, or the add-on was bought
        // elsewhere): show what's true now.
        if (error.kind === "already-included" || error.kind === "already-subscribed") {
          void refresh();
          return audioWords.errors[error.kind];
        }
        return null;
      },
    );
  // Renew (#2165): undo a cancellation, after one plain question.
  const [renewAsk, setRenewAsk] = useState(false);
  const [renewed, setRenewed] = useState(false);
  const askRef = useRef<HTMLParagraphElement>(null);
  const renewBoxRef = useRef<HTMLDivElement>(null);
  const renewedRef = useRef<HTMLParagraphElement>(null);
  // The question takes focus when it opens, so a keyboard or screen-reader user lands on it.
  useEffect(() => {
    if (renewAsk) askRef.current?.focus();
  }, [renewAsk]);
  const renew = (): void =>
    void run(
      async () => {
        if (!api.renewPlan) throw new ApiError("server");
        await api.renewPlan();
        setRenewAsk(false);
        setRenewed(true);
        await refresh();
        keepFocus(() => renewedRef.current);
      },
      (error) => {
        // Already renewed elsewhere, or the paid time ran out: show what's true now.
        if (error.kind === "not-ending") {
          setRenewAsk(false);
          void refresh();
        }
        return null;
      },
    );
  const portalUrl = portalLoginUrl();
  // Change plan opens on the plans to choose from; Fix my payment and Stop paying want the ordinary page.
  const portal = (flow?: "plans"): void =>
    void run(
      async () => go(await api.billingPortalUrl(flow)),
      (error) => {
        // The page is out of date (the plan stopped, or was never paid through the
        // company): show what's true now, with the plans to pick from.
        if (error.kind === "no-paid-plan") void refresh();
        return null;
      },
    );
  // Free access came after this page loaded (#806): show what's true now. Only Choose and
  // Start Try It can get has_free_access, and both come through here.
  const outOfDate = (error: ApiError): null => {
    if (error.kind === "free-access") void refresh();
    return null;
  };
  const choices = (title: string) => (
    <>
      <p>{title}</p>
      <PlanList
        choose={(id) => (
          <ActionButton
            busy={busy}
            kind="secondary"
            onClick={() => void run(async () => go(await api.checkoutUrl(id)), outOfDate)}
          >
            {text.choose(id)}
          </ActionButton>
        )}
      />
    </>
  );

  const access = me.access;
  if (access?.kind === "free") {
    // The API names the plan with stillPaying; the plan's own name is the backup.
    const paying = access.paidPlan ?? (plan ? planName(plan.id) : null);
    // Free access (#806): no hours bar, no prices and no payment buttons, except a way to
    // stop a paid plan that's still charging. Never why someone has free access.
    return (
      <section aria-labelledby="plan-heading" class="panel" id="plan">
        <h2 id="plan-heading">{text.planHeading}</h2>
        <Notice message={notice} />
        <p class="plan-name">
          {access.endsOn ? text.freeAccessUntil(access.endsOn) : text.freeAccess}
        </p>
        <p class="muted small">
          {text.freeNext}
          {access.endsOn && !access.stillPaying && ` ${text.freeEnds(access.endsOn)}`}
        </p>
        {access.hours && (
          <>
            <p class="hours-line">{hoursUsedLine(access.hours.used, access.hours.cap)}</p>
            <progress
              max={access.hours.cap}
              value={Math.min(Math.max(access.hours.used, 0), access.hours.cap)}
              aria-label={text.hoursBarLabel}
            />
            <p class="muted small">
              {text.freeHoursLeft(access.hours.used, access.hours.cap, access.hours.renewsOn)}
            </p>
          </>
        )}
        {!access.stillPaying && access.paidPlanEndsOn && (
          <p>{text.planEnds(access.paidPlan ?? null, access.paidPlanEndsOn)}</p>
        )}
        {access.stillPaying && (
          <>
            <p>{text.stillPaying(paying, plan?.renewsOn ?? null)}</p>
            <ActionButton busy={busy} kind="secondary" onClick={() => portal()}>
              {text.stopPaying}
            </ActionButton>
            {portalUrl && (
              <p class="muted small">
                <a href={portalUrl} rel="noopener noreferrer">{portalWords.account}</a>
              </p>
            )}
          </>
        )}
      </section>
    );
  }

  return (
    <section aria-labelledby="plan-heading" class="panel" id="plan">
      <h2 id="plan-heading">{text.planHeading}</h2>
      <Notice message={notice} />
      {plan === null ? (
        <>
          {me.firstPaymentFailed && (
            <div class="warn-box" role="alert">
              <p>{text.firstPaymentFailed}</p>
            </div>
          )}
          <p>{text.noPlan}</p>
          <ActionButton
            busy={busy}
            onClick={() =>
              void run(async () => {
                await api.startTryIt();
                await refresh();
              }, outOfDate)
            }
          >
            {text.startTryIt}
          </ActionButton>
          <p class="muted small">{text.startTryItNote}</p>
          {choices(text.orPick)}
        </>
      ) : (
        <>
          <p class="plan-name">{planLine(plan)}</p>
          {/* Always on the page, so the news is announced when it is filled in. */}
          <p role="status" tabIndex={-1} ref={renewedRef} class="small">
            {renewed ? text.renewed(planName(plan.id), plan.renewsOn) : ""}
          </p>
          {plan.status === "grace" && (
            <div class="warn-box" role="alert">
              <p>{text.grace(plan.graceEndsOn)}</p>
              <ActionButton busy={busy} onClick={() => portal()}>
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
                {hoursLeftLine(
                  plan.hoursUsed,
                  plan.hoursCap,
                  // The hours don't start again on a plan that is ending.
                  planState(plan) === "ending" ? null : plan.renewsOn,
                  plan.id,
                )}
              </p>
              {planState(plan) === "ending" && <p class="muted small">{text.endingNote}</p>}
              {plan.id === "try-it" ? (
                choices(text.pickPlan)
              ) : (
                <>
                  {/* Only over a paid plan that works now (active, or in the payment grace):
                      the API refuses the rest, and a lapsed plan is handled above. */}
                  <ActionButton busy={busy} onClick={extraHours}>
                    {text.addHours}
                  </ActionButton>
                  <p class="muted small">{text.addHoursNote}</p>
                  <ActionButton busy={busy} kind="secondary" onClick={() => portal("plans")}>
                    {text.changePlan}
                  </ActionButton>
                  <p class="muted small">{text.changePlanNote}</p>
                  {/* A cancelled plan that still works can be renewed (#2165): one question, then
                      the cancellation is undone and nothing is charged before the renewal date. */}
                  {plan.state === "ending" && api.renewPlan && (
                    <div ref={renewBoxRef}>
                      {renewAsk ? (
                        <div role="group" aria-label={text.renew} aria-describedby="renew-ask">
                          <p id="renew-ask" tabIndex={-1} ref={askRef}>
                            {plan.endsOn
                              ? text.renewAsk(planName(plan.id), plan.endsOn)
                              : text.renewAskNoDate(planName(plan.id))}
                          </p>
                          <ActionButton busy={busy} onClick={renew}>
                            {text.renewYes}
                          </ActionButton>
                          <ActionButton
                            busy={busy}
                            kind="secondary"
                            onClick={() => {
                              setRenewAsk(false);
                              // The question goes away, so focus goes back to Renew.
                              requestAnimationFrame(() =>
                                renewBoxRef.current?.querySelector("button")?.focus(),
                              );
                            }}
                          >
                            {text.renewNo}
                          </ActionButton>
                        </div>
                      ) : (
                        <ActionButton
                          busy={busy}
                          kind="secondary"
                          onClick={() => {
                            setRenewed(false);
                            setRenewAsk(true);
                          }}
                        >
                          {text.renew}
                        </ActionButton>
                      )}
                    </div>
                  )}
                  {RECAP_AUDIO_LIVE && me.recapAudio && (
                    <div class="addon" aria-labelledby="recap-audio-heading">
                      <h3 id="recap-audio-heading">{audioWords.heading}</h3>
                      {me.recapAudio.included ? (
                        <p>{audioWords.included}</p>
                      ) : me.recapAudio.addOn ? (
                        <p>{audioWords.addOn(me.recapAudio.endsOn)}</p>
                      ) : (
                        me.recapAudio.canBuy && (
                          <>
                            <ActionButton busy={busy} onClick={recapAudio}>
                              {audioWords.buy}
                            </ActionButton>
                            <p class="muted small">{audioWords.buyNote(audioPrice())}</p>
                          </>
                        )
                      )}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
      {portalUrl && (
        <p class="muted small">
          <a href={portalUrl} rel="noopener noreferrer">{portalWords.account}</a>
        </p>
      )}
    </section>
  );
}

/** Campaigns someone wants to hand you (#614). Accept re-checks your free slot on the server. */
function OffersSection({
  offers,
  free,
  onNews,
}: {
  offers: Offer[];
  free: boolean;
  onNews: (news: { text: string; ok: boolean }) => void;
}) {
  return (
    <section aria-labelledby="offers-heading" class="panel" id="offers">
      <h2 id="offers-heading">{text.offersHeading}</h2>
      <ul class="campaigns">
        {offers.map((o) => (
          <OfferRow key={o.id} offer={o} free={free} onNews={onNews} />
        ))}
      </ul>
    </section>
  );
}

function OfferRow({
  offer,
  free,
  onNews,
}: {
  offer: Offer;
  /** Free access: no plans to pick, so no "See my plan" (#806). */
  free: boolean;
  onNews: (news: { text: string; ok: boolean }) => void;
}) {
  const { api, refresh, noLimits } = useShared();
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
          setNoSlot(!free);
          return free ? text.acceptNoSlotFree : text.acceptNoSlot;
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
      <p>
        {(noLimits
          ? text.offerIncomingFree
          : free
            ? text.offerIncomingCapped
            : text.offerIncoming)(
          offer.personName,
          offer.campaignName,
          offer.serverName,
        )}
      </p>
      <p class="muted small">{text.offerExpires(offer.expiresAt)}</p>
      <Notice message={notice} />
      {noSlot && <a href="#plan">{text.seeMyPlan}</a>}
      <div class="row">
        <ActionButton
          busy={busy}
          onClick={() => answer(
              () => api.acceptOffer(offer.id),
              text.accepted(offer.campaignName, offer.personName),
            )}
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
  // Long lists fold (#1991): the newest few, and the rest behind one button. Not an inner scroll bar,
  // which is hard on phones and for keyboard and screen-reader users.
  const [all, setAll] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const offeredIds = new Set(me.offers.outgoing.map((o) => o.campaignId));
  // A campaign on a server DMbot can't reach is listed only once the website can delete it (#2555).
  const listed = CAMPAIGN_DELETE_LIVE ? me.campaigns : me.campaigns.filter((c) => c.serverReachable !== false);
  const { first, older } = foldCampaigns(listed, offeredIds);
  // After a hand-over the older ones can all be gone while the list is open: the button goes with
  // them, so the list closes too (and a later fold starts closed).
  const open = all && older.length > 0;
  useEffect(() => {
    if (all && older.length === 0) {
      setAll(false);
      heading.current?.focus();
    }
  }, [all, older.length]);
  const shown = open ? [...first, ...older] : first;
  return (
    <section aria-labelledby="campaigns-heading" class="panel" id="campaigns">
      <h2 id="campaigns-heading" ref={heading} tabIndex={-1}>
        {text.campaignsHeading}
      </h2>
      {done && (
        <p class="ok" role="status">
          {done}
        </p>
      )}
      {me.campaigns.length === 0 ? (
        <p>{text.noCampaigns}</p>
      ) : (
        <ul class="campaigns" id="campaigns-list">
          {shown.map((c) => (
            <CampaignRow
              key={c.id}
              campaign={c}
              tryIt={me.plan?.id === "try-it"}
              offer={me.offers.outgoing.find((o) => o.campaignId === c.id) ?? null}
              onWithdrawn={async (person) => {
                setDone(text.withdrawn(person));
                await refresh();
              }}
              onHandedOver={async (person) => {
                setDone(text.handOverDone(person.name));
                await refresh();
              }}
              onGone={async () => {
                setDone(campaignDeleteWords.problem["not-found"] ?? null);
                await refresh();
              }}
              onDeleted={async (name, channelsLeft) => {
                setDone(CAMPAIGN_DELETE_LIVE ? `${campaignDeleteWords.deleted(name)}${channelsLeft ? ` ${campaignDeleteWords.channelsLeft}` : ""}` : "");
                await refresh();
              }}
            />
          ))}
        </ul>
      )}
      {older.length > 0 && (
        <button
          type="button"
          class="button secondary"
          aria-expanded={open}
          aria-controls="campaigns-list"
          onClick={() => {
            setAll(!all);
            // Folding the list away would leave focus on a button that has moved: go back to the heading.
            if (all) heading.current?.focus();
          }}
        >
          {open ? text.showFewer : text.showOlder(older.length)}
        </button>
      )}
      {RECAPS_LIVE && me.campaigns.some((c) => c.role !== "player") && <a href="/account/recaps">{recapCard.linkToList}</a>}
    </section>
  );
}

function CampaignRow({
  campaign,
  offer,
  onWithdrawn,
  onHandedOver,
  onDeleted,
  onGone,
  tryIt = false,
}: {
  campaign: Campaign;
  /** A hand-over of this campaign you offered and nobody has answered yet. */
  offer: Offer | null;
  /** Gets the name of the person the offer was for, to say they'll be told. */
  onWithdrawn: (person: string) => Promise<void>;
  onHandedOver: (person: Person) => Promise<void>;
  /** The campaign was deleted from this card (#2555). */
  onDeleted: (name: string, channelsLeft: boolean) => Promise<void>;
  onGone: () => Promise<void>;
  /** A Try It account: a Try It campaign can't be handed over (#2534). */
  tryIt?: boolean;
}) {
  const { api, refresh, noLimits } = useShared();
  const { busy, notice, run } = useAction();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const formId = `handover-${campaign.id}`;

  return (
    <li data-campaign={campaign.id}>
      <p class="campaign-name">
        {/* Linked only when the API sends the campaign's address (#2416). */}
        {campaign.ref ? <a href={campaignHref(campaign.ref)}>{campaign.name}</a> : campaign.name}
      </p>
      <p class="muted small">
        {CAMPAIGN_DELETE_LIVE && campaign.serverReachable === false && !campaign.serverName ? campaignDeleteWords.unknownServer : campaign.serverName}
      </p>
      {CAMPAIGN_DELETE_LIVE && campaign.serverReachable === false && <p class="note small">{campaignDeleteWords.unreachable}</p>}
      <CampaignFacts campaign={campaign} />
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
                    await onWithdrawn(offer.personName);
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
      {HANDOVER_LIVE && campaign.role === "owner" && !tryIt && campaign.ref && api.offerHandover && api.takeBackHandover && (
        <EmailHandover campaign={campaign} refId={campaign.ref} />
      )}
      {/* Only the plan holder is offered Delete: the API says so with `canDelete` (a campaign with no owner yet
          can be deleted by its game masters); an API without it falls back to the owner. */}
      {CAMPAIGN_DELETE_LIVE && (campaign.canDelete ?? campaign.role === "owner") && campaign.ref && api.deleteCampaign && (
        <CampaignDelete
          api={api}
          refId={campaign.ref}
          name={campaign.name}
          tryIt={tryIt}
          onDeleted={(channelsLeft) => void onDeleted(campaign.name, channelsLeft)}
          onGone={() => void onGone()}
        />
      )}
      {!HANDOVER_LIVE && api.handoverCandidates && campaign.role === "owner" && !offer && people === null && (
        <ActionButton
          busy={busy}
          kind="secondary"
          onClick={() =>
            void run(async () => {
              setPeople((await api.handoverCandidates?.(campaign.id)) ?? []);
            }, campaignRefusal)
          }
        >
          {text.handOver}
        </ActionButton>
      )}
      {!HANDOVER_LIVE && people !== null && (
        <form
          id={formId}
          class="handover"
          onSubmit={(event) => {
            event.preventDefault();
            const person = people.find((p) => p.id === chosen);
            if (!person) return;
            void run(
              async () => {
                await api.handover?.(campaign.id, person.id);
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
                <p class="muted small">
                  {noLimits ? text.handOverNoteFree : text.handOverNote}
                </p>
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

/** Hand a campaign over by email (#2534; core #2532): the owner types the new owner's address, and sees only
 * "We've emailed the offer". While an offer is open it shows when it ends and a Take back button. The
 * answer never says whether the address has an account or a plan (#437). */
function EmailHandover({ campaign, refId }: { campaign: Campaign; refId: string }) {
  const { api, refresh } = useShared();
  const { busy, notice, run } = useAction();
  const [asking, setAsking] = useState(false);
  const [email, setEmail] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const saidRef = useRef<HTMLParagraphElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const [badEmail, setBadEmail] = useState(false);
  const words = handoverOwnerWords;
  const open = campaign.handover ?? null;
  const errorId = `handover-email-error-${campaign.id}`;
  const explain = (error: ApiError): string | null => {
    if (error.kind === "not-found") void refresh();
    setBadEmail(error.kind === "bad-email");
    if (error.kind === "bad-email") keepFocus(() => emailRef.current);
    return words.problem[error.kind] ?? words.problem["server"] ?? null;
  };
  return (
    <div class="stack">
      <div id={errorId}>
        <Notice message={notice} />
      </div>
      {/* One status line: what was just done, or the open offer once there is one. */}
      <p class="small" role="status" tabIndex={-1} ref={saidRef}>
        {open ? words.waiting(shortDateTime(open.sentAt), shortDateTime(open.expiresAt)) : said}
      </p>
      {open && !asking ? (
        <>
          <p class="muted small">{words.elsewhere}</p>
          <ActionButton
            busy={busy}
            kind="secondary"
            label={words.takeBackFor(campaign.name)}
            onClick={() =>
              void run(async () => {
                await api.takeBackHandover!(refId);
                setSaid(words.takenBack);
                await refresh();
                keepFocus(() => saidRef.current);
              }, explain)
            }
          >
            {words.takeBack}
          </ActionButton>
          <button
            type="button"
            class="button secondary"
            onClick={() => {
              setAsking(true);
              keepFocus(() => emailRef.current);
            }}
          >
            {words.sendElsewhere}
          </button>
        </>
      ) : asking ? (
        <form
          class="handover"
          noValidate // the API checks the address and our own plain words say what is wrong
          onSubmit={(event) => {
            event.preventDefault();
            if (email.trim() === "") return;
            void run(async () => {
              // A light check first, so an obvious slip doesn't use one of the day's five offers.
              if (!/^\S+@\S+\.\S+$/.test(email.trim())) throw new ApiError("bad-email");
              await api.offerHandover!(refId, email.trim());
              setAsking(false);
              setEmail("");
              setSaid(words.emailed);
              await refresh();
              keepFocus(() => saidRef.current);
            }, explain);
          }}
        >
          <fieldset disabled={busy}>
            <legend>{words.question(campaign.name)}</legend>
            <label class="field">
              {words.label}
              <input
                ref={emailRef}
                type="email"
                inputMode="email"
                autocomplete="off"
                autoCapitalize="none"
                spellcheck={false}
                aria-invalid={badEmail}
                aria-describedby={badEmail ? errorId : undefined}
                value={email}
                onInput={(e) => {
                  setEmail((e.target as HTMLInputElement).value);
                  setBadEmail(false);
                }}
              />
            </label>
            <p class="muted small">{words.note}</p>
            {open && <p class="muted small">{words.replaces}</p>}
          </fieldset>
          <div class="row">
            <button type="submit" class="button" disabled={busy || email.trim() === ""} aria-busy={busy}>
              {busy ? words.sending : words.send}
            </button>
            <button
              type="button"
              class="button secondary"
              disabled={busy}
              onClick={() => {
                setAsking(false);
                setBadEmail(false);
                keepFocus(() => startRef.current);
              }}
            >
              {words.cancel}
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          class="button secondary"
          ref={startRef}
          aria-label={words.buttonFor(campaign.name)}
          onClick={() => {
            setAsking(true);
            setSaid(null);
            keepFocus(() => emailRef.current);
          }}
        >
          {words.button}
        </button>
      )}
    </div>
  );
}

function ServersSection({ me }: { me: Me }) {
  const { api } = useShared();
  return (
    <section aria-labelledby="servers-heading" class="panel">
      <h3 id="servers-heading">{text.serversHeading}</h3>
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

/** The way in to the Shared rules page (#2460). Only a person who runs a campaign has rules to
 * share or use, and only an API that can say what they share has the page. */
function SharedRulesSection({ me }: { me: Me }) {
  const { api } = useShared();
  if (!api.sharedRules || !me.campaigns.some((c) => c.role !== "player")) return null;
  return (
    <section aria-labelledby="shared-rules-heading" class="panel">
      <h2 id="shared-rules-heading">{sharingCard.heading}</h2>
      <p>{sharingCard.lead}</p>
      <p>
        <a href="/account/sharing">{sharingCard.link}</a>
      </p>
    </section>
  );
}

/** My characters (#1164): the person's own sheets, read-only. Loaded once the page is up; an
 * API older than this, or a failed load, shows nothing rather than an error. */
function CharactersSection() {
  const { api } = useShared();
  const [list, setList] = useState<Character[] | null>(null);
  useEffect(() => {
    let live = true;
    api
      .characters?.()
      .then((found) => live && setList(found))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api]);
  if (list === null) return null;
  return (
    <section aria-labelledby="characters-heading" class="panel">
      <h2 id="characters-heading">{text.charactersHeading}</h2>
      {list.length === 0 ? (
        <p>{text.charactersNone}</p>
      ) : (
        <>
          <p class="muted small">{text.characterChange}</p>
          <ul class="campaigns">
            {list.map((c, i) => {
              const facts = [
                c.level !== null ? text.characterLevel(c.level) : null,
                c.maxHp !== null ? text.characterHp(c.maxHp) : null,
                c.ac !== null ? text.characterAc(c.ac) : null,
              ].filter((part) => part !== null);
              return (
                <li key={`${c.serverName}-${c.campaign}-${c.name}-${i}`} data-character>
                  <p class="plan-name">{c.name ?? text.characterUnread}</p>
                  {c.name === null && <p class="muted small">{text.characterUnreadNote}</p>}
                  {c.classLine && <p>{c.classLine}</p>}
                  <p class="muted small">{text.characterWhere(c.serverName, c.campaign)}</p>
                  {facts.length > 0 && (
                    <p class="small facts">
                      {facts.map((fact, n) => (
                        <span key={fact}>
                          {n > 0 && " · "}
                          <span class="nowrap">{fact}</span>
                        </span>
                      ))}
                    </p>
                  )}
                  {(c.typedIn || c.readAt) && (
                    <p class="muted small">
                      {[c.typedIn ? text.characterTyped : null, c.readAt ? text.characterRead(c.readAt) : null]
                        .filter((part) => part !== null)
                        .join(" ")}
                    </p>
                  )}
                  {c.sheetLink && (
                    <a href={c.sheetLink} target="_blank" rel="noopener noreferrer">
                      {text.characterOpen}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

/** The message for coming back from adding a Discord account (?discord=added etc.), or null (#2534). */
function discordResult(search: string): string | null {
  const result = new URLSearchParams(search).get("discord");
  return result === null ? null : (discordAccountsWords.result[result] ?? discordAccountsWords.result["failed"] ?? null);
}

/** Your Discord accounts (#2534): the list by name, "Add a Discord account" (Discord's own sign-in,
 * never a typed number) and Remove with one confirmation, never for the last one. The list is the API's;
 * after a removal it is read again from `me`. */
function DiscordAccountsSection({ accounts, resultText }: { accounts: DiscordAccount[]; resultText: string | null }) {
  const { api, refresh } = useShared();
  const { busy, notice, run } = useAction();
  const [asking, setAsking] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const saidRef = useRef<HTMLParagraphElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const words = discordAccountsWords;
  useEffect(forgetDiscordResult, []); // show the result once: a reload must not repeat it
  const last = accounts.length <= 1;
  return (
    <section aria-labelledby="discord-accounts-heading" class="panel">
      <h3 id="discord-accounts-heading">{words.heading}</h3>
      <p>{words.lead}</p>
      {resultText && (
        <p class={resultText === words.result["added"] ? "ok" : "warn"} role="status">
          {resultText}
        </p>
      )}
      <Notice message={notice} />
      <p class="ok" role="status" tabIndex={-1} ref={saidRef}>
        {said}
      </p>
      <ul class="stack">
        {accounts.map((a) => (
          <li key={a.key} class="stack">
            <p>
              <strong>{a.name}</strong> {a.current && <span class="muted small">{words.you}</span>}
            </p>
            {a.canRemove && !last && !a.current && asking !== a.key && (
              <button
                type="button"
                class="button secondary"
                aria-label={words.removeFor(a.name)}
                disabled={busy}
                onClick={() => {
                  setAsking(a.key);
                  setSaid(null);
                  keepFocus(() => keepRef.current); // the safe choice first
                }}
              >
                {words.remove}
              </button>
            )}
            {asking === a.key && (
              <div class="stack" role="group" aria-label={words.removeFor(a.name)}>
                <p>{words.confirmFor(a.name)}</p>
                <div class="row">
                  <button
                    type="button"
                    class="button"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        async () => {
                          try {
                            await api.removeDiscordAccount!(a.key);
                          } catch (error) {
                            // Already gone (removed in another tab): say so, and read the list again so
                            // the page matches what is true.
                            if (error instanceof ApiError && error.kind === "not-found") {
                              setAsking(null);
                              await refresh();
                            }
                            throw error;
                          }
                          setAsking(null);
                          setSaid(words.removed);
                          await refresh();
                          keepFocus(() => saidRef.current);
                        },
                        (error) => words.problem[error.kind] ?? words.problem["default"] ?? null,
                      )
                    }
                  >
                    {busy ? words.removing : words.confirmYes}
                  </button>
                  <button type="button" class="button secondary" ref={keepRef} disabled={busy} onClick={() => setAsking(null)}>
                    {words.keep}
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
      {last ? (
        <p class="muted small">{words.lastOne}</p>
      ) : (
        accounts.some((a) => a.current && a.canRemove) && <p class="muted small">{words.cantRemoveNow}</p>
      )}
      <p>
        <a class="button" href={api.addDiscordUrl!()}>
          {words.add}
        </a>
      </p>
      <p class="muted small">{words.addNote}</p>
    </section>
  );
}

/** Take ?discord= out of the address once its result is shown (as for ?install=). */
function forgetDiscordResult(): void {
  if (typeof window === "undefined" || !window.location.search.includes("discord=")) return;
  const url = new URL(window.location.href);
  url.searchParams.delete("discord");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

/** Your DMbot ID and experience points (#1231). The ID is an account number, not a secret. */
function IdSection({ me }: { me: Me }) {
  return (
    <section aria-labelledby="id-heading" class="panel">
      <h3 id="id-heading">{text.idHeading}</h3>
      <p>{text.idLine(me.dmbotId ?? "")}</p>
      <p class="muted small">{text.idNote}</p>
      {me.experiencePoints !== undefined && (
        <>
          <p>{text.pointsLine(me.experiencePoints)}</p>
          <p class="muted small">{text.pointsNote}</p>
        </>
      )}
    </section>
  );
}

/** Moving to a new Discord account (#1231): paid plans get a move code by email, never on screen.
 * The steps for using it show only after it has been sent. */
function MoveSection() {
  const { api } = useShared();
  const { busy, notice, run } = useAction();
  const [sent, setSent] = useState(false);
  return (
    <section aria-labelledby="move-heading" class="panel">
      <h3 id="move-heading">{text.moveHeading}</h3>
      <p>{text.moveText}</p>
      <Notice message={notice} />
      {sent && (
        <p class="ok" role="status">
          {text.moveSent}
        </p>
      )}
      <ActionButton
        busy={busy}
        kind="secondary"
        onClick={() =>
          void run(
            async () => {
              await api.getMoveCode?.();
              setSent(true);
            },
            (error) =>
              ({
                "not-ready": text.moveNotReady,
                "slow-down": text.moveWait,
                "no-email": text.moveNoEmail,
                "too-soon": text.moveTooSoon,
                "email-failed": text.moveEmailFailed,
              })[error.kind as string] ?? null,
          )
        }
      >
        {text.moveButton}
      </ActionButton>
    </section>
  );
}

/** Where DMbot may record you (#1151): each server with Stop recording me, the same one short
 * warning and one confirmation as the bot's Menu. Starting again stays in Discord. */
function RecordingSection({ entries }: { entries: RecordingEntry[] }) {
  const [done, setDone] = useState<string[]>([]);
  // The row goes with the refresh after a stop, so what was done is said here, above the list.
  if (entries.length === 0 && done.length === 0) return null;
  return (
    <section aria-labelledby="recording-heading" class="panel">
      <h3 id="recording-heading">{text.recordingHeading}</h3>
      {done.map((line) => (
        <p key={line} class="ok" role="status">
          {line}
        </p>
      ))}
      <ul class="campaigns">
        {entries.map((e) => (
          <RecordingRow
            key={e.serverId}
            entry={e}
            onStopped={() => setDone((now) => [...now, text.stoppedRecording(e.serverName)])}
          />
        ))}
      </ul>
    </section>
  );
}

function RecordingRow({ entry, onStopped }: { entry: RecordingEntry; onStopped: () => void }) {
  const { api, refresh } = useShared();
  const { busy, notice, run, clear } = useAction();
  const [asking, setAsking] = useState(false);
  if (!entry.on) {
    return (
      <li data-recording={entry.serverId}>
        <p>{text.recordingOld(entry.serverName)}</p>
      </li>
    );
  }
  return (
    <li data-recording={entry.serverId}>
      <p>{text.recordingLine(entry.serverName, entry.agreedOn)}</p>
      <Notice message={notice} />
      {asking ? (
        <>
          <p role="alert">{text.stopWarning(entry.serverName)}</p>
          <div class="row">
            <ActionButton
              busy={busy}
              onClick={() =>
                void run(async () => {
                  await api.stopRecording(entry.serverId);
                  onStopped();
                  await refresh();
                })
              }
            >
              {text.stopYes}
            </ActionButton>
            <button
              type="button"
              class="button secondary"
              disabled={busy}
              onClick={() => {
                clear();
                setAsking(false);
              }}
            >
              {text.stopKeep}
            </button>
          </div>
        </>
      ) : (
        <button type="button" class="button secondary" onClick={() => setAsking(true)}>
          {text.stopRecording}
        </button>
      )}
    </li>
  );
}

/** Save a file the page fetched, under the name the browser's download offers. */
function saveFile(file: Blob, name: string): void {
  const address = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = address;
  link.download = name;
  link.click();
  // Some browsers start the download after the click returns, so the address lives a moment.
  setTimeout(() => URL.revokeObjectURL(address), 1000);
}

/** Download my data (#1150): one file with what DMbot holds about this person. Fetched here, so
 * "once a minute" and "sign in again" are said in words, not shown as a raw error page. */
function ExportSection() {
  const { api } = useShared();
  const { busy, notice, run } = useAction();
  return (
    <section aria-labelledby="export-heading" class="panel">
      <h3 id="export-heading">{text.exportHeading}</h3>
      <p>{text.exportWhat}</p>
      <ActionButton
        busy={busy}
        kind="secondary"
        onClick={() =>
          void run(
            async () => saveFile(await api.downloadExport(), "dmbot-my-data.json"),
            (error) => (error.kind === "slow-down" ? text.exportTooSoon : null),
          )
        }
      >
        {text.exportButton}
      </ActionButton>
      <Notice message={notice} />
      <p class="muted small">{text.exportWait}</p>
    </section>
  );
}

function DeleteSection({
  paidPlan,
  free,
  stillPaying,
  onDeleted,
}: {
  paidPlan: boolean;
  free: boolean;
  /** Free access while a paid plan is still being paid for. */
  stillPaying: boolean;
  onDeleted: () => void;
}) {
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
      <h3 id="delete-heading">{text.deleteHeading}</h3>
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
          {(free
            ? [
                stillPaying ? text.deleteWarningFreePaying : text.deleteWarningFree,
                ...text.deleteWarning.slice(1),
              ]
            : text.deleteWarning
          ).map((line) => (
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

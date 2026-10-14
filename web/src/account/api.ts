/**
 * The web API the account page talks to (#435, core/src/dmbot/web/), as the site sees it.
 *
 * Security rules (#434, #435):
 * - Sign-in happens on the API: the browser goes to /auth/discord/start and comes back to
 *   /account with an httpOnly session cookie. The site never sees a Discord token, and
 *   nothing is ever put in a URL or in localStorage.
 * - Every call sends the cookie (credentials: "include") and a custom header, so a plain
 *   cross-site form can't make one (the API rejects requests without it).
 * - Discord ids are strings: they don't fit in a JavaScript number.
 */
import type { PlanId } from "../content/pricing";

export type PlanStatus = "active" | "grace" | "lapsed";

export interface MyPlan {
  id: PlanId;
  status: PlanStatus;
  /** Listening hours used this billing month (can have a fraction). */
  hoursUsed: number;
  hoursCap: number;
  /** ISO date the hours start again (the next billing day), or null. */
  renewsOn: string | null;
  /** ISO date a failed payment must be fixed by (status "grace"), or null. */
  graceEndsOn: string | null;
  /** ISO date a cancelled plan ends (it works until then and does not renew), if it was. */
  endsOn?: string;
}

export interface Campaign {
  id: string;
  name: string;
  serverName: string;
  /** ISO date and time of the last session, or null if never played. */
  lastPlayedAt: string | null;
  status: "active" | "paused";
  /** "owner": uses your hours. "co-dm": you help run it. */
  role: "owner" | "co-dm";
}

/** A Discord server you manage. */
export interface Server {
  id: string;
  name: string;
  hasDmbot: boolean;
  /** DMbot joined through a plain invite link and nobody has said who added it. */
  canLink: boolean;
  installedByYou: boolean;
}

export interface Person {
  id: string;
  name: string;
}

/** A campaign hand-over waiting for an answer (#614, #437). It ends after 7 days. */
export interface Offer {
  id: string;
  campaignId: string;
  campaignName: string;
  serverName: string;
  /** Incoming: who offers it. Outgoing: who it's offered to. */
  personName: string;
  /** ISO date and time the offer ends if nobody answers. */
  expiresAt: string;
}

/** Free access (#771, #806): never says why someone has it. */
export interface Access {
  kind: "free" | "paid" | "none";
  /** ISO date free access ends, or missing when it doesn't. */
  endsOn?: string;
  /** Free access while a paid plan still works and is still charged. */
  stillPaying?: boolean;
  /** That plan's name, as the API words it. */
  paidPlan?: string;
  /** ISO date a cancelled paid plan under free access ends (then it is not "still paying"). */
  paidPlanEndsOn?: string;
  /** Free access with no hours or campaign limit at all (the API says so; it is not guessed). */
  unlimited?: boolean;
  /** Hours for free access that has a cap (a grant "like Guild"); missing when it has none. */
  hours?: { used: number; cap: number; renewsOn: string | null };
}

export interface Me {
  user: Person;
  plan: MyPlan | null;
  /** Missing from an API older than #804: the site can go live first. */
  access?: Access;
  campaigns: Campaign[];
  servers: Server[];
  // The API also sends `installs` (servers this person added DMbot to); the servers list
  // already says "You added DMbot here", so the page doesn't use it (#497).
  /** A first payment failed and none has gone through since (#922): the page says to try again. */
  firstPaymentFailed?: boolean;
  /** Where DMbot may record you. Missing from an API older than #1151. */
  recording?: RecordingEntry[];
  /** Hand-overs offered to you, and the ones you offered (#614). */
  offers: { incoming: Offer[]; outgoing: Offer[] };
}

/** One of the person's own characters (#1164), read-only. Fields are missing when the sheet has
 * not been read yet (a link only) or does not hold them. */
export interface Character {
  serverName: string;
  campaign: string | null;
  name: string | null;
  classLine: string | null;
  level: number | null;
  maxHp: number | null;
  ac: number | null;
  /** The online sheet's link, if there is one. */
  sheetLink: string | null;
  typedIn: boolean;
  readAt: string | null;
}

/** A server where this person said yes to recording (#1151). `on` is false when their yes was
 * given under older wording: DMbot records nobody until they say yes again in Discord. */
export interface RecordingEntry {
  serverId: string;
  serverName: string;
  /** ISO date they said yes. */
  agreedOn: string;
  on: boolean;
}

/** Why a call failed, in a form the page can turn into plain words. */
export type ApiErrorKind =
  | "network" // couldn't reach the API
  | "signed-out" // the session ended
  | "no-free-slot" // accepting a hand-over: you have no free campaign slot
  | "not-allowed" // not your campaign, or not allowed right now
  | "sign-in-again" // acting for a server or deleting needs a sign-in from the last day
  | "try-it-used" // Try It was used before
  | "has-plan" // a plan already works (Try It or a second checkout)
  | "payments-off" // the payment company isn't set up yet
  | "already-linked" // someone else already said they added DMbot to this server
  | "not-installed" // DMbot isn't in that server yet
  | "offer-gone" // the hand-over offer ended, expired or was answered already
  | "confirm-again" // the delete confirmation ran out (10 minutes) or belongs elsewhere
  | "no-paid-plan" // the billing page needs a paid plan
  | "free-access" // free access: there's nothing to pay for
  | "slow-down" // too many tries from this address (the API limits sign-in, billing, delete)
  | "server"; // anything else

export class ApiError extends Error {
  constructor(readonly kind: ApiErrorKind) {
    super(kind);
    this.name = "ApiError";
  }
}

export interface AccountApi {
  /** Who is signed in, or null when nobody is. */
  me(): Promise<Me | null>;
  /** Where the "Sign in with Discord" button goes. */
  signInUrl(): string;
  signOut(): Promise<void>;
  /** End every sign-in this person has, this one too (#1163). */
  signOutEverywhere(): Promise<void>;
  /** Start the free Try It plan (no card). */
  startTryIt(): Promise<void>;
  /** The payment company's checkout page for a paid plan. */
  checkoutUrl(plan: PlanId): Promise<string>;
  /** The payment company's page to change plan, fix a payment or stop. */
  billingPortalUrl(): Promise<string>;
  /** The payment company's page to buy more hours for this month (a paid, working plan). */
  extraHoursUrl(): Promise<string>;
  /** The file for "Download my data": the person's own data as JSON (#1150). Fetched from the
   * page, so a refusal (once a minute, or signed out) can be said in words. */
  downloadExport(): Promise<Blob>;
  /** The person's own characters. Missing from an API older than #1164. */
  characters?(): Promise<Character[]>;
  /** Stop recording me in one server (#1151). Starting again stays in Discord. */
  stopRecording(serverId: string): Promise<void>;
  /** Where "Add DMbot" goes: the API, which sends the browser on to Discord. */
  installUrl(serverId: string): string;
  /** Say you added DMbot to a server it joined through a plain link. */
  linkServer(serverId: string): Promise<void>;
  /** The campaign's other DMs, who can be offered it (never filtered by plan: the owner
   * must not learn whether someone pays, #437). Missing until the API has the route
   * (#704's follow-up); the page then shows no Hand over button. */
  handoverCandidates?(campaignId: string): Promise<Person[]>;
  /** Offer a campaign to one of them. Missing until the API has the route, as above. */
  handover?(campaignId: string, toUserId: string): Promise<void>;
  /** Take a campaign offered to you (needs a free campaign slot now). */
  acceptOffer(offerId: string): Promise<void>;
  /** Say no to a campaign offered to you. */
  declineOffer(offerId: string): Promise<void>;
  /** Take back a hand-over you offered. */
  withdrawOffer(offerId: string): Promise<void>;
  /** Step 1 of deleting the account: returns a short-lived token for step 2. */
  requestDelete(): Promise<string>;
  /** Step 2: delete the account and its data now. */
  confirmDelete(token: string): Promise<void>;
}

const errorKinds: Record<string, ApiErrorKind> = {
  no_free_slot: "no-free-slot",
  not_allowed: "not-allowed",
  sign_in_again: "sign-in-again",
  try_it_used: "try-it-used",
  try_it_has_plan: "has-plan",
  has_paid_plan: "has-plan",
  payments_off: "payments-off",
  already_linked: "already-linked",
  not_installed: "not-installed",
  confirm_again: "confirm-again",
  no_paid_plan: "no-paid-plan",
  offer_gone: "offer-gone",
  has_free_access: "free-access",
  slow_down: "slow-down",
  busy: "slow-down",
};

/** The real API over HTTP. `base` is the API's address, e.g. "https://api.example". */
export function httpApi(base: string, fetcher: typeof fetch = fetch): AccountApi {
  const root = base.replace(/\/+$/, "");

  async function call(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
    asFile = false,
  ): Promise<unknown> {
    let response: Response;
    try {
      const init: RequestInit = {
        method,
        credentials: "include",
        headers: { "X-DMbot-Request": "1", Accept: "application/json" },
      };
      if (body !== undefined) {
        init.headers = { ...init.headers, "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      response = await fetcher(`${root}${path}`, init);
    } catch {
      throw new ApiError("network");
    }
    if (response.status === 401) throw new ApiError("signed-out");
    if (!response.ok) {
      let code = "";
      try {
        code = String(((await response.json()) as { error?: unknown }).error ?? "");
      } catch {
        // No JSON body: a plain server error.
      }
      throw new ApiError(errorKinds[code] ?? (response.status === 403 ? "not-allowed" : "server"));
    }
    if (response.status === 204) return null;
    return asFile ? response.blob() : response.json();
  }

  const url = async (path: string, body?: unknown): Promise<string> =>
    ((await call("POST", path, body)) as { url: string }).url;

  return {
    async me() {
      try {
        return (await call("GET", "/me")) as Me;
      } catch (error) {
        if (error instanceof ApiError && error.kind === "signed-out") return null;
        throw error;
      }
    },
    signInUrl: () => `${root}/auth/discord/start`,
    signOut: async () => {
      await call("POST", "/auth/logout");
    },
    signOutEverywhere: async () => {
      await call("POST", "/me/sign-out-everywhere");
    },
    startTryIt: async () => {
      await call("POST", "/plan/try-it");
    },
    checkoutUrl: (plan) => url("/billing/checkout", { plan }),
    billingPortalUrl: () => url("/billing/portal"),
    extraHoursUrl: () => url("/billing/extra-hours"),
    downloadExport: async () => (await call("GET", "/me/export", undefined, true)) as Blob,
    characters: async () =>
      ((await call("GET", "/me/characters")) as { characters: Character[] }).characters,
    installUrl: (serverId) => `${root}/install?server_id=${encodeURIComponent(serverId)}`,
    linkServer: async (serverId) => {
      await call("POST", `/servers/${encodeURIComponent(serverId)}/link`);
    },
    // No handoverCandidates or handover yet: the API has no route to make an offer from
    // the site until #704's follow-up, so the Hand over button stays hidden.
    acceptOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/accept`);
    },
    declineOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/decline`);
    },
    withdrawOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/withdraw`);
    },
    stopRecording: async (serverId) => {
      await call("POST", `/me/recording/${encodeURIComponent(serverId)}/stop`);
    },
    requestDelete: async () =>
      ((await call("POST", "/account/delete/request")) as { confirm_token: string })
        .confirm_token,
    confirmDelete: async (token) => {
      await call("POST", "/account/delete/confirm", { confirm_token: token });
    },
  };
}

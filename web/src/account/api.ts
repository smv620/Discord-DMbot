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

export interface Me {
  user: Person;
  plan: MyPlan | null;
  campaigns: Campaign[];
  servers: Server[];
  // The API also sends `installs` (servers this person added DMbot to); the servers list
  // already says "You added DMbot here", so the page doesn't use it (#497).
}

/** Why a call failed, in a form the page can turn into plain words. */
export type ApiErrorKind =
  | "network" // couldn't reach the API
  | "signed-out" // the session ended
  | "no-free-slot" // hand-over: that person's plan is full
  | "not-allowed" // not your campaign, or not allowed right now
  | "sign-in-again" // acting for a server or deleting needs a sign-in from the last day
  | "try-it-used" // Try It was used before
  | "has-plan" // a plan already works (Try It or a second checkout)
  | "payments-off" // the payment company isn't set up yet
  | "already-linked" // someone else already said they added DMbot to this server
  | "not-installed" // DMbot isn't in that server yet
  | "confirm-again" // the delete confirmation ran out (10 minutes) or belongs elsewhere
  | "no-paid-plan" // the billing page needs a paid plan
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
  /** Start the free Try It plan (no card). */
  startTryIt(): Promise<void>;
  /** The payment company's checkout page for a paid plan. */
  checkoutUrl(plan: PlanId): Promise<string>;
  /** The payment company's page to change plan, fix a payment or stop. */
  billingPortalUrl(): Promise<string>;
  /** Where "Add DMbot" goes: the API, which sends the browser on to Discord. */
  installUrl(serverId: string): string;
  /** Say you added DMbot to a server it joined through a plain link. */
  linkServer(serverId: string): Promise<void>;
  /** People who can take over a campaign (they have a plan with room for it). */
  handoverCandidates(campaignId: string): Promise<Person[]>;
  handover(campaignId: string, toUserId: string): Promise<void>;
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
};

/** The real API over HTTP. `base` is the API's address, e.g. "https://api.example". */
export function httpApi(base: string, fetcher: typeof fetch = fetch): AccountApi {
  const root = base.replace(/\/+$/, "");

  async function call(method: "GET" | "POST", path: string, body?: unknown): Promise<unknown> {
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
    return response.json();
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
    startTryIt: async () => {
      await call("POST", "/plan/try-it");
    },
    checkoutUrl: (plan) => url("/billing/checkout", { plan }),
    billingPortalUrl: () => url("/billing/portal"),
    installUrl: (serverId) => `${root}/install?server_id=${encodeURIComponent(serverId)}`,
    linkServer: async (serverId) => {
      await call("POST", `/servers/${encodeURIComponent(serverId)}/link`);
    },
    // Hand-over paths are a guess until #437 ships the API: check them against it then.
    handoverCandidates: async (campaignId) =>
      (await call("GET", `/campaigns/${encodeURIComponent(campaignId)}/handover-candidates`)) as
        Person[],
    handover: async (campaignId, toUserId) => {
      await call("POST", `/campaigns/${encodeURIComponent(campaignId)}/handover`, {
        to_user_id: toUserId,
      });
    },
    requestDelete: async () =>
      ((await call("POST", "/account/delete/request")) as { confirm_token: string })
        .confirm_token,
    confirmDelete: async (token) => {
      await call("POST", "/account/delete/confirm", { confirm_token: token });
    },
  };
}

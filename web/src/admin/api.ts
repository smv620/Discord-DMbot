/**
 * The web API's admin sign-in (#772, core/src/dmbot/web/admin_api.py), as the site sees it.
 *
 * The admin session is an HttpOnly, SameSite=Strict cookie the site never sees. Every
 * call sends it and the site's X-DMbot-Request header; sign-out (and every later admin
 * change) also sends the session's CSRF token, which /admin/me hands back.
 */
export interface AdminMe {
  email: string;
  csrf: string;
}

/** Why something failed, in a form the page turns into plain words. */
export type AdminProblem =
  | "wrong"
  | "busy"
  | "down"
  | "off"
  | "signed-out" // the admin session ended (an hour idle or 12 hours in all)
  // Free access refusals (#773), each naming what to fix:
  | "bad-id"
  | "bad-level"
  | "bad-date"
  | "past-date"
  | "long-note"
  | "no-grant";

export type GrantLevel = "guild" | "unlimited";

/** Free access given on the admin page (#773). Times are Unix seconds. */
export interface AdminGrant {
  discordId: string;
  level: GrantLevel;
  /** When it stops (the end of the chosen day), or null for no end. */
  endsAt: number | null;
  note: string;
  grantedBy: string;
  grantedAt: number;
}

export interface AdminLogEntry {
  at: number;
  by: string;
  action: "grant" | "change" | "revoke";
  discordId: string;
}

export interface GrantsView {
  /** Ids on the server's free list: shown, never changed here. */
  free: string[];
  grants: AdminGrant[];
  log: AdminLogEntry[];
}

export interface GrantRequest {
  discordId: string;
  level: GrantLevel;
  /** "2026-12-31", or null for no end. */
  endsOn: string | null;
  note: string;
}

export class AdminApiError extends Error {
  constructor(readonly kind: AdminProblem) {
    super(kind);
    this.name = "AdminApiError";
  }
}

/** Which sign-ins the server has set up. */
export interface AdminWays {
  google: boolean;
  password: boolean;
}

export interface AdminApi {
  /** Who is signed in, or null. */
  me(): Promise<AdminMe | null>;
  ways(): Promise<AdminWays>;
  /** Where "Sign in with Google" goes: the API, which sends the browser on to Google. */
  googleUrl(): string;
  signIn(email: string, password: string): Promise<void>;
  signOut(csrf: string): Promise<void>;
  /** Free access (#773). */
  grants(): Promise<GrantsView>;
  give(csrf: string, request: GrantRequest): Promise<"grant" | "change">;
  revoke(csrf: string, discordId: string): Promise<void>;
}

const grantProblems: Record<string, AdminProblem> = {
  bad_id: "bad-id",
  bad_level: "bad-level",
  bad_date: "bad-date",
  past_date: "past-date",
  long_note: "long-note",
  no_grant: "no-grant",
};

/** A refused free access call as a problem the page can word. */
async function grantProblem(response: Response): Promise<AdminProblem> {
  if (response.status === 401) return "signed-out";
  let code = "";
  try {
    code = String(((await response.json()) as { error?: unknown }).error ?? "");
  } catch {
    // No JSON body: a plain server error.
  }
  return grantProblems[code] ?? "down";
}

export function httpAdminApi(base: string, fetcher: typeof fetch = fetch): AdminApi {
  const root = base.replace(/\/+$/, "");

  async function call(path: string, init: RequestInit = {}): Promise<Response> {
    try {
      return await fetcher(`${root}${path}`, {
        ...init,
        credentials: "include",
        headers: { "X-DMbot-Request": "1", Accept: "application/json", ...init.headers },
        signal: AbortSignal.timeout(30_000),
      });
    } catch {
      throw new AdminApiError("down");
    }
  }

  return {
    async me() {
      const response = await call("/admin/me");
      if (response.status === 401) return null;
      // No admin routes at all: ADMIN_EMAILS is empty on the server.
      if (response.status === 404) throw new AdminApiError("off");
      if (!response.ok) throw new AdminApiError("down");
      return (await response.json()) as AdminMe;
    },
    async ways() {
      const response = await call("/admin/auth/ways");
      if (response.status === 404) throw new AdminApiError("off");
      if (!response.ok) throw new AdminApiError("down");
      return (await response.json()) as AdminWays;
    },
    googleUrl: () => `${root}/admin/auth/google/start`,
    async signIn(email, password) {
      const response = await call("/admin/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (response.status === 401 || response.status === 400) throw new AdminApiError("wrong");
      // Too many tries at once: not the owner's password's fault.
      if (response.status === 503) throw new AdminApiError("busy");
      if (!response.ok) throw new AdminApiError("down");
    },
    async signOut(csrf) {
      const response = await call("/admin/auth/logout", {
        method: "POST",
        headers: { "X-Admin-CSRF": csrf },
      });
      // Already signed out is fine: the goal is reached.
      if (!response.ok && response.status !== 401) throw new AdminApiError("down");
    },
    async grants() {
      const response = await call("/admin/grants");
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
      return (await response.json()) as GrantsView;
    },
    async give(csrf, request) {
      const response = await call("/admin/grants", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrf },
        body: JSON.stringify(request),
      });
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
      return ((await response.json()) as { action: "grant" | "change" }).action;
    },
    async revoke(csrf, discordId) {
      const response = await call(`/admin/grants/${encodeURIComponent(discordId)}/revoke`, {
        method: "POST",
        headers: { "X-Admin-CSRF": csrf },
      });
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
    },
  };
}

/** The pretend API for preview builds (PUBLIC_API_BASE=mock): any password works. */
export function pretendAdminApi(): AdminApi {
  let me: AdminMe | null = null;
  const view: GrantsView = { free: ["100000000000000001"], grants: [], log: [] };
  const now = (): number => Math.floor(Date.now() / 1000);
  return {
    grants: async () => structuredClone(view),
    give: async (_csrf, request) => {
      if (!/^[0-9]{17,20}$/.test(request.discordId)) throw new AdminApiError("bad-id");
      const action = view.grants.some((g) => g.discordId === request.discordId)
        ? "change"
        : "grant";
      view.grants = view.grants.filter((g) => g.discordId !== request.discordId);
      view.grants.unshift({
        discordId: request.discordId,
        level: request.level,
        endsAt: request.endsOn ? Date.parse(`${request.endsOn}T00:00:00Z`) / 1000 + 86400 : null,
        note: request.note.trim(),
        grantedBy: me?.email ?? "admin@example.com",
        grantedAt: now(),
      });
      view.log.unshift({
        at: now(),
        by: me?.email ?? "admin@example.com",
        action,
        discordId: request.discordId,
      });
      return action;
    },
    revoke: async (_csrf, discordId) => {
      if (!view.grants.some((g) => g.discordId === discordId)) throw new AdminApiError("no-grant");
      view.grants = view.grants.filter((g) => g.discordId !== discordId);
      view.log.unshift({ at: now(), by: me?.email ?? "admin@example.com", action: "revoke", discordId });
    },
    me: async () => me,
    ways: async () => ({ google: true, password: true }),
    googleUrl: () => "#demo-google",
    signIn: async (email) => {
      me = { email, csrf: "pretend" };
    },
    signOut: async () => {
      me = null;
    },
  };
}

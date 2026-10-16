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
  | "bad-note"
  | "already-free"
  | "no-grant"
  | "no-ticket" // support ticket list: it is gone (the 12-month sweep, or the person deleted their account)
  | "stale"; // refused for a reason a reload fixes (an old CSRF token, a bad body)

export type GrantLevel = "guild" | "unlimited";

/** Free access given on the admin page (#773). Times are Unix seconds. */
export interface AdminGrant {
  discordId: string;
  /** The name they signed in to the site with, if that was lately; else null. */
  name: string | null;
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
  name: string | null;
}

/** Someone on the server's free list: shown, never changed here. */
export interface FreePerson {
  discordId: string;
  name: string | null;
}

export interface GrantsView {
  free: FreePerson[];
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

export type TicketStatus = "new" | "answered" | "closed";
/** What the Tickets list shows: one status, or all (#1375). */
export type TicketFilter = TicketStatus | "all";
/** How many tickets have each status, over all of them, for the filter buttons (#1375). */
export type TicketCounts = Record<TicketStatus, number>;
export interface TicketList {
  tickets: SupportTicketRow[];
  counts: TicketCounts;
}

/** A private support ticket from a paying person (#1340). Only the admin sees the words. */
export interface SupportTicketRow {
  id: number;
  /** Unix seconds. */
  at: number;
  discordId: string;
  dmbotId: string;
  /** The name they signed in to the site with, if that was lately; else null. */
  name: string | null;
  /** The plan's id when it was sent. */
  plan: string;
  subject: string;
  message: string;
  source: string;
  status: TicketStatus;
  /** Where the team replies: the account's email, read for this page only. Null: it has none. */
  email: string | null;
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
  give(
    csrf: string,
    request: GrantRequest,
  ): Promise<{ action: "grant" | "change"; name: string | null }>;
  revoke(csrf: string, discordId: string): Promise<void>;
  /** Support tickets, newest first, with the count for each status (#1340, #1375). */
  tickets(filter?: TicketFilter): Promise<TicketList>;
  setTicketStatus(csrf: string, id: number, status: TicketStatus): Promise<void>;
}

const grantProblems: Record<string, AdminProblem> = {
  bad_id: "bad-id",
  bad_level: "bad-level",
  bad_date: "bad-date",
  past_date: "past-date",
  long_note: "long-note",
  bad_note: "bad-note",
  already_free: "already-free",
  no_grant: "no-grant",
  no_ticket: "no-ticket",
  bad_status: "stale",
  // The grant writer's own refusals, worded by the site.
  sign_in_again: "signed-out",
  // The page can't send these unless it's out of date (another tab, an old token).
  bad_request: "stale",
  not_allowed: "stale",
  too_long: "stale",
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
  // No admin routes at all: ADMIN_EMAILS was emptied while the page was open.
  if (code === "not_found") return "off";
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
      return (await response.json()) as { action: "grant" | "change"; name: string | null };
    },
    async revoke(csrf, discordId) {
      const response = await call(`/admin/grants/${encodeURIComponent(discordId)}/revoke`, {
        method: "POST",
        headers: { "X-Admin-CSRF": csrf },
      });
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
    },
    async tickets(filter = "all") {
      const response = await call(`/admin/support?status=${encodeURIComponent(filter)}`);
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
      const body = (await response.json()) as { tickets: SupportTicketRow[]; counts?: TicketCounts };
      const c = body.counts;
      if (c && [c.new, c.answered, c.closed].every((n) => typeof n === "number")) {
        return { tickets: body.tickets, counts: c };
      }
      // An API older than #1375 sends every ticket and no counts: count and filter here, so the
      // buttons still work until it is deployed. (It sends only its newest tickets, so until then
      // the counts and an old "Needs a reply" ticket past that limit can be missing.)
      const counts: TicketCounts = { new: 0, answered: 0, closed: 0 };
      for (const t of body.tickets) counts[t.status] += 1;
      return {
        tickets: filter === "all" ? body.tickets : body.tickets.filter((t) => t.status === filter),
        counts,
      };
    },
    async setTicketStatus(csrf, id, status) {
      const response = await call(`/admin/support/${encodeURIComponent(String(id))}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrf },
        body: JSON.stringify({ status }),
      });
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
    },
  };
}

/** The pretend API for preview builds (PUBLIC_API_BASE=mock): any password works. */
export function pretendAdminApi(): AdminApi {
  let me: AdminMe | null = null;
  const view: GrantsView = {
    free: [{ discordId: "100000000000000001", name: "Demo owner" }],
    grants: [],
    log: [],
  };
  const now = (): number => Math.floor(Date.now() / 1000);
  const tickets: SupportTicketRow[] = [
    {
      id: 2,
      at: now() - 3600,
      discordId: "100000000000000002",
      dmbotId: "1P0Z4F6N2K9",
      name: "Demo player",
      plan: "table",
      subject: "My invoice shows the wrong plan",
      message: "Hello,\nthe last invoice says Guild but I'm on Table.",
      source: "website",
      status: "new",
      email: "player@example.com",
    },
    {
      id: 1,
      at: now() - 86_400 * 3,
      discordId: "100000000000000003",
      dmbotId: "1P0Z4F6N2KA",
      name: null,
      plan: "guild",
      subject: "Thank you",
      message: "Just saying the last session went well.",
      source: "website",
      status: "answered",
      email: null,
    },
  ];
  return {
    tickets: async (filter = "all") => {
      const counts: TicketCounts = { new: 0, answered: 0, closed: 0 };
      for (const t of tickets) counts[t.status] += 1;
      const shown = tickets.filter((t) => filter === "all" || t.status === filter);
      return { tickets: structuredClone(shown), counts };
    },
    setTicketStatus: async (_csrf, id, status) => {
      const ticket = tickets.find((t) => t.id === id);
      if (!ticket) throw new AdminApiError("no-ticket");
      ticket.status = status;
    },
    grants: async () => structuredClone(view),
    give: async (_csrf, request) => {
      if (!/^[0-9]{17,20}$/.test(request.discordId)) throw new AdminApiError("bad-id");
      const action = view.grants.some((g) => g.discordId === request.discordId)
        ? "change"
        : "grant";
      view.grants = view.grants.filter((g) => g.discordId !== request.discordId);
      view.grants.unshift({
        discordId: request.discordId,
        name: null,
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
        name: null,
      });
      return { action, name: null };
    },
    revoke: async (_csrf, discordId) => {
      if (!view.grants.some((g) => g.discordId === discordId)) throw new AdminApiError("no-grant");
      view.grants = view.grants.filter((g) => g.discordId !== discordId);
      view.log.unshift({
        at: now(),
        by: me?.email ?? "admin@example.com",
        action: "revoke",
        discordId,
        name: null,
      });
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

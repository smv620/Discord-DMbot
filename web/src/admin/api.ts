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
  | "bad-reply" // the reply is empty or too long (#1432)
  | "reply-off" // the mail side is not set up, so a reply cannot be sent (#1432)
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
  /** Page 1 of Recent changes, newest first (#2199). */
  log: AdminLogEntry[];
  /** How many pages the log has. Missing from an API older than #2199: then there is only
   * the one list, and no page bar. */
  logPages?: number;
}

/** One page of Recent changes (#2199). */
export interface LogPage {
  log: AdminLogEntry[];
  page: number;
  pages: number;
}

export interface GrantRequest {
  discordId: string;
  level: GrantLevel;
  /** "2026-12-31", or null for no end. */
  endsOn: string | null;
  note: string;
}

/** "escalated": waiting for a person on the team (the helper gave it up, or the person asked). */
export type TicketStatus = "new" | "answered" | "closed" | "escalated";
/** What the Tickets list shows: one status, or all (#1375). */
export type TicketFilter = TicketStatus | "all";
/** How many tickets have each status, over all of them, for the filter buttons (#1375). */
export type TicketCounts = Record<TicketStatus, number>;
/** How support went in the last seven days (#1480): counts only. Missing from an older API. */
export interface TicketWeek {
  questions: number;
  helper: number;
  team: number;
  waiting: number;
  reasons: Record<string, number>;
}
export interface TicketList {
  tickets: SupportTicketRow[];
  counts: TicketCounts;
  week?: TicketWeek;
}

/** One message after the first, in the order they came (#1432): the person's emailed reply, DMbot's
 * helper's answer, or the team's. `at` is Unix seconds. */
export interface TicketReply {
  id: number;
  who: "customer" | "ai" | "person";
  text: string;
  at: number;
}

/** A private support ticket from a signed-in person (#1340). Only the admin sees the words. */
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
  /** The thread after the first message (#1432). Missing from an API older than that. */
  replies?: TicketReply[];
  /** Why a person has it, when the API says (a fixed code, #1432): "unhappy", "asked",
   * "limit", "no-email" or "helper-down". Missing: nothing is shown. */
  reason?: string | null;
  /** When it was handed to the team, Unix seconds (#1494); null when never handed over. Missing
   * from an API older than that: the wait counts from `at`. */
  handedOverAt?: number | null;
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
  /** One page of Recent changes, from 1; a page that isn't there is a "stale" refusal. */
  grantsLog?(page: number): Promise<LogPage>;
  give(
    csrf: string,
    request: GrantRequest,
  ): Promise<{ action: "grant" | "change"; name: string | null }>;
  revoke(csrf: string, discordId: string): Promise<void>;
  /** Support tickets, newest first, with the count for each status (#1340, #1375). */
  tickets(filter?: TicketFilter): Promise<TicketList>;
  setTicketStatus(csrf: string, id: number, status: TicketStatus): Promise<void>;
  /** The team's reply: emailed to the person and kept on the ticket, which becomes answered
   * (#1432). */
  replyToTicket(csrf: string, id: number, text: string): Promise<void>;
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
  bad_page: "stale", // a page that was there is gone: the page goes back to the first
  no_ticket: "no-ticket",
  bad_text: "bad-reply",
  reply_off: "reply-off",
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
    async grantsLog(page) {
      const response = await call(`/admin/grants/log?page=${encodeURIComponent(String(page))}`);
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
      return (await response.json()) as LogPage;
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
      const body = (await response.json()) as {
        tickets: SupportTicketRow[];
        counts?: TicketCounts;
        week?: TicketWeek;
      };
      const week = readWeek(body.week);
      const c = body.counts;
      if (c && [c.new, c.answered, c.closed].every((n) => typeof n === "number")) {
        // An API older than #1431 has no escalated count: none.
        return { tickets: body.tickets, counts: { ...c, escalated: c.escalated ?? 0 }, ...(week ? { week } : {}) };
      }
      // An API older than #1375 sends every ticket and no counts: count and filter here, so the
      // buttons still work until it is deployed. (It sends only its newest tickets, so until then
      // the counts and an old "Needs a reply" ticket past that limit can be missing.)
      const counts: TicketCounts = { new: 0, answered: 0, closed: 0, escalated: 0 };
      for (const t of body.tickets) counts[t.status] += 1;
      return {
        tickets: filter === "all" ? body.tickets : body.tickets.filter((t) => t.status === filter),
        counts,
        ...(week ? { week } : {}),
      };
    },
    async replyToTicket(csrf, id, text) {
      const response = await call(`/admin/support/${encodeURIComponent(String(id))}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrf },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) throw new AdminApiError(await grantProblem(response));
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

/** The week's numbers if the API sent them and they are all numbers; otherwise none (an older API). */
function readWeek(raw: TicketWeek | undefined): TicketWeek | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const numbers = [raw.questions, raw.helper, raw.team, raw.waiting];
  if (!numbers.every((n) => typeof n === "number" && Number.isFinite(n))) return undefined;
  const reasons: Record<string, number> = {};
  for (const [code, n] of Object.entries(raw.reasons ?? {})) {
    if (typeof n === "number" && Number.isFinite(n)) reasons[code] = n;
  }
  return { questions: raw.questions, helper: raw.helper, team: raw.team, waiting: raw.waiting, reasons };
}

/** Entries on a page of Recent changes in the preview, as on the real API (#2199). */
const MOCK_PAGE = 20;

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
      status: "escalated",
      email: "player@example.com",
      reason: "unhappy",
      replies: [
        { id: 1, who: "ai", text: "Try the Billing page, then tell us if it still looks wrong.", at: now() - 3000 },
        { id: 2, who: "customer", text: "That did not help, I want a person.", at: now() - 1800 },
      ],
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
      const counts: TicketCounts = { new: 0, answered: 0, closed: 0, escalated: 0 };
      for (const t of tickets) counts[t.status] += 1;
      const shown = tickets.filter((t) => filter === "all" || t.status === filter);
      const week: TicketWeek = {
        questions: tickets.length,
        helper: 0,
        team: 0,
        waiting: counts.new + counts.escalated,
        reasons: { unhappy: 1 },
      };
      return { tickets: structuredClone(shown), counts, week };
    },
    replyToTicket: async (_csrf, id, text) => {
      const ticket = tickets.find((t) => t.id === id);
      if (!ticket) throw new AdminApiError("no-ticket");
      ticket.replies = [...(ticket.replies ?? []), { id: Date.now(), who: "person", text, at: now() }];
      ticket.status = "answered";
    },
    setTicketStatus: async (_csrf, id, status) => {
      const ticket = tickets.find((t) => t.id === id);
      if (!ticket) throw new AdminApiError("no-ticket");
      ticket.status = status;
    },
    grants: async () => {
      const first = structuredClone(view);
      first.log = first.log.slice(0, MOCK_PAGE);
      first.logPages = Math.max(1, Math.ceil(view.log.length / MOCK_PAGE));
      return first;
    },
    grantsLog: async (page) => {
      const pages = Math.max(1, Math.ceil(view.log.length / MOCK_PAGE));
      if (!Number.isInteger(page) || page < 1 || page > pages) throw new AdminApiError("stale");
      return {
        log: structuredClone(view.log.slice((page - 1) * MOCK_PAGE, page * MOCK_PAGE)),
        page,
        pages,
      };
    },
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

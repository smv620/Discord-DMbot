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
export type AdminProblem = "wrong" | "down";

export class AdminApiError extends Error {
  constructor(readonly kind: AdminProblem) {
    super(kind);
    this.name = "AdminApiError";
  }
}

export interface AdminApi {
  /** Who is signed in, or null. */
  me(): Promise<AdminMe | null>;
  /** Where "Sign in with Google" goes: the API, which sends the browser on to Google. */
  googleUrl(): string;
  signIn(email: string, password: string): Promise<void>;
  signOut(csrf: string): Promise<void>;
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
      if (!response.ok) throw new AdminApiError("down");
      return (await response.json()) as AdminMe;
    },
    googleUrl: () => `${root}/admin/auth/google/start`,
    async signIn(email, password) {
      const response = await call("/admin/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (response.status === 401 || response.status === 400) throw new AdminApiError("wrong");
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
  };
}

/** The pretend API for preview builds (PUBLIC_API_BASE=mock): any password works. */
export function pretendAdminApi(): AdminApi {
  let me: AdminMe | null = null;
  return {
    me: async () => me,
    googleUrl: () => "#demo-google",
    signIn: async (email) => {
      me = { email, csrf: "pretend" };
    },
    signOut: async () => {
      me = null;
    },
  };
}

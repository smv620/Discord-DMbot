// The calls of the "People you play with" page (#4226): the routes core serves at /me/players and /me/remembered-by (web/app.py). In a
// file of its own so the other account pages carry none of it. A refusal keeps the server's word in `PlayersError.kind`.
export interface RememberedPlayer {
  player: string;
  name: string;
  since: number;
  last: number;
  campaigns: number;
}
export interface RememberedBy {
  dm: string;
  name: string;
  since: number;
  campaigns: number;
}

export class PlayersError extends Error {
  readonly kind: string;
  constructor(kind: string) {
    super(kind);
    this.kind = kind;
  }
}

export interface PlayersApi {
  mine(): Promise<RememberedPlayer[]>;
  /** Removes one player from the signed-in DM's list; `gone` when they were already gone. */
  forget(player: string): Promise<void>;
  by(): Promise<RememberedBy[]>;
  /** One DM's memory of me, or every DM's with `"all"`. */
  clear(dm: string): Promise<void>;
}

export function httpPlayers(base: string, fetcher: typeof fetch = fetch): PlayersApi {
  const root = base.replace(/\/+$/, "");
  async function call(method: "GET" | "POST", path: string): Promise<Record<string, unknown>> {
    let response: Response;
    try {
      response = await fetcher(`${root}${path}`, { method, credentials: "include", headers: { "X-DMbot-Request": "1", Accept: "application/json" } });
    } catch {
      throw new PlayersError("network");
    }
    if (response.status === 401) throw new PlayersError("signed_out");
    if (!response.ok) {
      let kind = "failed";
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") kind = body.detail;
      } catch {
        /* keep "failed" */
      }
      throw new PlayersError(kind);
    }
    return (await response.json().catch(() => ({}))) as Record<string, unknown>;
  }
  const enc = encodeURIComponent;
  return {
    async mine() {
      const got = await call("GET", "/me/players");
      return Array.isArray(got["items"]) ? (got["items"] as RememberedPlayer[]) : [];
    },
    async forget(player) {
      await call("POST", `/me/players/${enc(player)}/forget`);
    },
    async by() {
      const got = await call("GET", "/me/remembered-by");
      return Array.isArray(got["items"]) ? (got["items"] as RememberedBy[]) : [];
    },
    async clear(dm) {
      await call("POST", `/me/remembered-by/${enc(dm)}/clear`);
    },
  };
}

/** A small invented table for `PUBLIC_API_BASE=mock` and the tests: two players, one DM who remembers me. */
export function mockPlayers(): PlayersApi & { calls: string[] } {
  const calls: string[] = [];
  let mine: RememberedPlayer[] = [
    { player: "100000000000000001", name: "Eric", since: 1_700_000_000, last: 1_730_000_000, campaigns: 3 },
    { player: "100000000000000002", name: "", since: 1_720_000_000, last: 1_725_000_000, campaigns: 1 },
  ];
  let by: RememberedBy[] = [{ dm: "100000000000000009", name: "Belleros", since: 1_700_000_000, campaigns: 2 }];
  return {
    calls,
    async mine() {
      calls.push("mine");
      return mine.map((p) => ({ ...p }));
    },
    async forget(player) {
      calls.push(`forget:${player}`);
      if (!mine.some((p) => p.player === player)) throw new PlayersError("gone");
      mine = mine.filter((p) => p.player !== player);
    },
    async by() {
      calls.push("by");
      return by.map((d) => ({ ...d }));
    },
    async clear(dm) {
      calls.push(`clear:${dm}`);
      by = dm === "all" ? [] : by.filter((d) => d.dm !== dm);
    },
  };
}

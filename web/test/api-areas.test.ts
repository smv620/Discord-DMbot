// The real API's calls are in files fetched on demand (#3010). A call missing from its list would be missing
// from the page that asks for it, so the lists and the files are kept in step here, and every call still shows.
import { describe, expect, it } from "vitest";

import { ACCOUNT_METHODS, CAMPAIGN_METHODS, NAMES_METHODS, RECAPS_METHODS, httpApi, type ApiHelpers } from "../src/account/api";

const noop = (async () => null) as unknown as ApiHelpers["call"];
const helpers: ApiHelpers = { call: noop, restoreCall: noop, waitForAnswer: async () => ({}) as never, root: "/x", url: async () => "" };

const areas = {
  account: [ACCOUNT_METHODS, () => import("../src/account/api-account")],
  campaign: [CAMPAIGN_METHODS, () => import("../src/account/api-campaign")],
  names: [NAMES_METHODS, () => import("../src/account/api-names")],
  recaps: [RECAPS_METHODS, () => import("../src/account/api-recaps")],
} as const;

describe("the API's on-demand areas", () => {
  for (const [name, [methods, load]] of Object.entries(areas)) {
    it(`${name}: the file holds exactly the calls its list names`, async () => {
      const mod = await load();
      expect(Object.keys(mod.default(helpers)).sort()).toEqual([...methods].sort());
    });
  }

  it("keeps every call on the API, each once, whether or not its file has loaded", () => {
    const api = httpApi("/x", async () => new Response("{}"));
    const listed = [...ACCOUNT_METHODS, ...CAMPAIGN_METHODS, ...NAMES_METHODS, ...RECAPS_METHODS];
    expect(new Set(listed).size).toBe(listed.length); // no call in two areas
    for (const name of listed) expect(typeof (api as unknown as Record<string, unknown>)[name], name).toBe("function");
    for (const name of ["me", "signInUrl", "signOut", "addDiscordUrl", "installUrl"]) {
      expect(typeof (api as unknown as Record<string, unknown>)[name], name).toBe("function"); // the ones a page needs at once
    }
  });

  it("answers a call through the area's file, with the call's own arguments", async () => {
    const seen: string[] = [];
    const api = httpApi("/x", async (url) => {
      seen.push(String(url));
      return new Response(JSON.stringify({ names: [], kinds: [], total: 0 }), { status: 200, headers: { "content-type": "application/json" } });
    });
    await api.allNames?.("1.a", { q: "m", kind: "", sort: "name", page: 1 });
    expect(seen[0]).toContain("/me/campaigns/1.a/all-names?");
    expect(seen[0]).toContain("q=m");
  });
});

// Undo this upload over the real client, against core's routes (0417, #3879): queued with a POST, polled at the same path, the
// count the bot took back read from `undone`, and core's refusal and outcome words turned into the page's own.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

/** A pretend network: each request gets the next answer (a status and a body) and is remembered. */
function net(answers: { status?: number; body: unknown }[]) {
  const queue = [...answers];
  const seen: string[] = [];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push(`${init?.method ?? "GET"} ${String(url).replace("https://api.example", "")} ${typeof init?.body === "string" ? init.body : ""}`.trim());
    const next = queue.shift() ?? { body: {} };
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
const source = { kind: "lore", id: "up1" } as const;
const ID = "a".repeat(32);

describe("undoUpload against core", () => {
  it("queues the press, polls the same path and reads how many changes were taken back", async () => {
    await import("../src/account/api-names");
    const { api, seen } = net([{ status: 202, body: { id: ID } }, { body: { state: "working", progress: null } }, { body: { state: "done", undone: 7, progress: null } }]);
    const got = await api.undoUpload!("1.cmp1", source);
    expect(got).toEqual({ took: 7 });
    expect(seen[0]).toBe('POST /me/campaigns/1.cmp1/upload-notice/undo {"kind":"lore","id":"up1"}');
    expect(seen[1]).toBe(`GET /me/campaigns/1.cmp1/upload-notice/undo/${ID}`);
  });

  it("turns the door's refusals and the bot's outcome words into the page's own", async () => {
    await import("../src/account/api-names");
    const refused = async (status: number, error: string): Promise<string> => {
      const { api } = net([{ status, body: { error } }]);
      try {
        await api.undoUpload!("1.cmp1", source);
      } catch (e) {
        return (e as ApiError).kind;
      }
      return "no error";
    };
    expect(await refused(409, "undone")).toBe("undo-done");
    expect(await refused(409, "too_late")).toBe("undo-late");
    const failed = async (error: string): Promise<string> => {
      const { api } = net([{ status: 202, body: { id: ID } }, { body: { state: "failed", error } }]);
      try {
        await api.undoUpload!("1.cmp1", source);
      } catch (e) {
        return (e as ApiError).kind;
      }
      return "no error";
    };
    expect(await failed("already")).toBe("undo-done");
    expect(await failed("too_late")).toBe("undo-late");
    expect(await failed("lost")).toBe("server");
    expect(await failed("failed")).toBe("server");
  });
});

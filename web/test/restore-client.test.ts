// @vitest-environment happy-dom
// The calls of the Load a backup page (#2745): the copy goes as the raw bytes of the file (never as JSON, never in
// the address), the API's codes reach the page as its own kinds, and "none yet" is null, not an error.
import { describe, expect, it } from "vitest";

import { httpApi } from "../src/account/api";

function fake(status: number, body: unknown) {
  const seen: { url: string; method: string; body: unknown; type: string | null }[] = [];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen.push({ url: String(url), method: String(init?.method), body: init?.body, type: headers.get("Content-Type") });
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
const file = new File([new Uint8Array([31, 139, 8, 0])], "copy.dmbot.json.gz");

describe("loading a backup", () => {
  it("stages the file as raw bytes, with the campaign to replace in the query only", async () => {
    const { api, seen } = fake(202, { id: "a".repeat(32), name: "Lost World", partsWanted: 2 });
    const made = await api.restoreStage!("200", file, "200.own");
    expect(made).toEqual({ id: "a".repeat(32), name: "Lost World", partsWanted: 2 });
    expect(seen[0]).toMatchObject({ url: "https://api.example/me/restores/200?replace=200.own", method: "POST", type: "application/octet-stream" });
    expect(seen[0]!.body).toBeInstanceOf(Blob);
  });

  it("a new campaign has no query", async () => {
    const { api, seen } = fake(202, { id: "a".repeat(32), name: "x", partsWanted: 0 });
    await api.restoreStage!("200", file);
    expect(seen[0]!.url).toBe("https://api.example/me/restores/200");
  });

  it("sends a transcripts file by its number, then goes", async () => {
    const { api, seen } = fake(200, { received: 1 });
    await api.restorePart!("200", "b".repeat(32), 2, file);
    expect(seen[0]).toMatchObject({ url: `https://api.example/me/restores/200/${"b".repeat(32)}/parts/2`, method: "PUT" });
    await api.restoreGo!("200", "b".repeat(32), true);
    expect(seen[1]).toMatchObject({ url: `https://api.example/me/restores/200/${"b".repeat(32)}/go`, method: "POST" });
    expect(JSON.parse(String(seen[1]!.body))).toEqual({ without: true });
  });

  it("reads the latest, and none yet is null", async () => {
    const { api, seen } = fake(404, { error: "not_found" });
    expect(await api.restoreStatus!("200")).toBeNull();
    expect(seen[0]).toMatchObject({ url: "https://api.example/me/restores/200", method: "GET" });
  });

  it("clears one", async () => {
    const { api, seen } = fake(204, null);
    await api.restoreCancel!("200", "c".repeat(32));
    expect(seen[0]).toMatchObject({ url: `https://api.example/me/restores/200/${"c".repeat(32)}`, method: "DELETE" });
  });

  it.each([
    [422, "not_a_backup", "not-a-backup"],
    [422, "too_many_parts", "too-many-parts"],
    [413, "too_big", "too-big"],
    [409, "missing", "files-missing"],
    [409, "not_receiving", "not-receiving"],
    [422, "bad_part", "bad-part"],
    [429, "too_soon", "too-soon"],
    [409, "busy", "slow-down"],
  ])("a %i %s refusal is the page's %s", async (status, code, kind) => {
    const { api } = fake(status, { error: code });
    await expect(api.restoreStage!("200", file)).rejects.toMatchObject({ kind });
  });
});

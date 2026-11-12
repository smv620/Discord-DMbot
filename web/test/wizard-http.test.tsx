/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The wizard's real calls (#4025) against a stubbed fetch: each route's shape (the paths and bodies core serves at /me/wizard/{guild}),
// the answers filled in where the server leaves them out, every refusal word kept as the server said it and shown as its plain line,
// a file sent as base64, and the finish card through to the campaign.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Wizard from "../src/account/Wizard";
import { toBase64, WizardError } from "../src/account/wizard-api";
import { httpWizard } from "../src/account/wizard-http";
import { wizardWords as w } from "../src/content/wizard-words";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

type Reply = { status?: number; body?: unknown };
/** A pretend network: each "METHOD /path" answers what the table says (a list answers in turn), and every call is recorded. */
function net(routes: Record<string, Reply | Reply[]>) {
  const seen: { key: string; body: unknown }[] = [];
  const fetcher = (async (url: string, init: RequestInit) => {
    const key = `${init.method} ${new URL(url).pathname}`;
    seen.push({ key, body: typeof init.body === "string" ? JSON.parse(init.body) : undefined });
    const entry = routes[key];
    const reply = Array.isArray(entry) ? (entry.length > 1 ? entry.shift() : entry[0]) : entry;
    if (!reply) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    return new Response(JSON.stringify(reply.body ?? {}), { status: reply.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  return { seen, api: httpWizard("https://api.example", fetcher) };
}
const G = "300000000000000001";
const draft = (over: Record<string, unknown> = {}) => ({ step: 3, answers: { name: "Frostmaiden", characters: [{ full: "Brannoc Stonebelt", partyName: "Brannoc", sheetUrl: "", player: "pending" }] }, rightsShared: true, rightsRuleset: false, createdAt: 1, expiresAt: 999, now: 5, documents: [{ id: "d1", slot: "lore", name: "lore.txt", source: "upload", pages: 0, words: 40, status: "ok", reason: "" }], ...over });

describe("the routes", () => {
  it("get: {} is no draft, and a draft has every answer filled in", async () => {
    expect(await net({ [`GET /me/wizard/${G}`]: { body: {} } }).api.get(G)).toBeNull();
    const got = await net({ [`GET /me/wizard/${G}`]: { body: draft() } }).api.get(G);
    expect(got?.step).toBe(3);
    expect(got?.answers.name).toBe("Frostmaiden");
    expect(got?.answers.supplements).toBeNull(); // (left out by the server: untouched, the defaults hold)
    expect(got?.answers.target).toBe("2024");
    expect(got?.rights).toEqual({ shared: true, ruleset: false });
    expect(got?.documents[0]).toMatchObject({ id: "d1", slot: "lore", words: 40, status: "ok" });
  });

  it("start posts the name then reads the draft; set puts the step and answers; skip posts the step", async () => {
    const { api, seen } = net({ [`POST /me/wizard/${G}`]: { body: { status: "ok" } }, [`GET /me/wizard/${G}`]: { body: draft() }, [`PUT /me/wizard/${G}/step`]: { body: { status: "ok" } }, [`POST /me/wizard/${G}/skip`]: { body: { status: "ok" } } });
    await api.start(G, "Frostmaiden");
    await api.set(G, 4, { supplements: ["tasha"] });
    await api.skip(G, 5);
    expect(seen.find((s) => s.key === `POST /me/wizard/${G}`)?.body).toEqual({ name: "Frostmaiden" });
    expect(seen.find((s) => s.key === `PUT /me/wizard/${G}/step`)?.body).toEqual({ step: 4, answers: { supplements: ["tasha"] } });
    expect(seen.find((s) => s.key === `POST /me/wizard/${G}/skip`)?.body).toEqual({ step: 5 });
  });

  it("no request carries a step above 9: the last page is the page's own and is shown, not stored", async () => {
    const { api, seen } = net({ [`PUT /me/wizard/${G}/step`]: { body: { status: "ok" } }, [`GET /me/wizard/${G}`]: { body: draft({ step: 9 }) }, [`POST /me/wizard/${G}/skip`]: { body: { status: "ok" } } });
    const last = await api.set(G, 10, { name: "Frostmaiden" });
    await api.skip(G, 10);
    expect(last.step).toBe(10); // (shown from the page's own state)
    const steps = seen.map((s) => (s.body as { step?: number } | undefined)?.step).filter((x): x is number => typeof x === "number");
    expect(steps.length).toBe(2);
    expect(Math.max(...steps)).toBe(9);
  });

  it("a file goes as base64 and only its name, pages and words come back", async () => {
    const { api, seen } = net({ [`POST /me/wizard/${G}/documents`]: { body: { id: "d2", slot: "secrets", name: "plot.txt", pages: 0, words: 9, status: "ok", reason: "" } } });
    const doc = await api.docAdd(G, "secrets", { name: "plot.txt", source: "upload", data: toBase64(new TextEncoder().encode("hello")) });
    expect(doc).toMatchObject({ id: "d2", words: 9 });
    expect(seen[0]!.body).toEqual({ slot: "secrets", name: "plot.txt", data: "aGVsbG8=" });
  });

  it("remove, rights, page text, finish, finish state and discard", async () => {
    const { api, seen } = net({
      [`DELETE /me/wizard/${G}/documents/d1`]: { body: { status: "ok" } },
      [`POST /me/wizard/${G}/rights`]: { body: { status: "ok" } },
      [`POST /me/wizard/${G}/page-text`]: { body: { name: "Winter", characters: ["Brannoc Stonebelt"] } },
      [`POST /me/wizard/${G}/finish`]: { body: { id: "f1" } },
      [`GET /me/wizard/${G}/finish`]: [{ body: {} }, { body: { id: "f1", state: "working", stage: "lore", part: 1, ofParts: 2, campaign: "c9", error: "", createdAt: 10, movedAt: 20, now: 30 } }],
      [`DELETE /me/wizard/${G}`]: { body: { status: "ok" } },
    });
    await api.docRemove(G, "d1");
    await api.rights(G, "shared");
    expect(await api.pageText(G, "Characters:\nBrannoc Stonebelt")).toEqual({ name: "Winter", characters: [{ full: "Brannoc Stonebelt", partyName: "Brannoc" }] });
    expect(await api.finish(G)).toBe("f1");
    expect(await api.finishState(G)).toBeNull(); // ({} is "not started")
    expect(await api.finishState(G)).toMatchObject({ state: "working", stage: "lore", part: 1, ofParts: 2, campaignRef: `${G}.c9`, createdAt: 10, movedAt: 20, serverNow: 30 });
    await api.discard(G);
    expect(seen.find((s) => s.key === `POST /me/wizard/${G}/rights`)?.body).toEqual({ kind: "shared" });
  });

  it("toBase64 round-trips bytes of any size", () => {
    const bytes = new Uint8Array(100_000).map((_, i) => i % 251);
    const back = Uint8Array.from(atob(toBase64(bytes)), (c) => c.charCodeAt(0));
    expect(back.length).toBe(bytes.length);
    expect(back[99_999]).toBe(bytes[99_999]);
  });
});

describe("the refusals", () => {
  const cases: [number, string][] = [[402, "no_room"], [409, "needs_name"], [409, "needs_rights"], [413, "too_big"], [413, "too_many"], [404, "no_draft"], [422, "bad_document"], [429, "too_soon"], [422, "bad_key"]];
  it.each(cases)("%i %s keeps the server's word", async (status, word) => {
    const { api } = net({ [`POST /me/wizard/${G}/finish`]: { status, body: { detail: word } } });
    await expect(api.finish(G)).rejects.toMatchObject({ kind: word });
  });
  it("a sign-out and a dead network have their own words", async () => {
    await expect(net({ [`GET /me/wizard/${G}`]: { status: 401 } }).api.get(G)).rejects.toMatchObject({ kind: "signed_out" });
    const down = httpWizard("https://api.example", (async () => Promise.reject(new Error("x"))) as unknown as typeof fetch);
    await expect(down.get(G)).rejects.toBeInstanceOf(WizardError);
  });

  const shown: [number, string, string][] = [
    [402, "no_room", w.noRoom], [409, "needs_name", w.needsName], [429, "too_soon", w.tooFast], [404, "no_draft", w.expired],
    [422, "bad_fallback", w.rules.same], [422, "bad_patch", w.badAnswer], [413, "too_big", w.docs.unreadable["too_big"]!], [403, "not_allowed", w.notAllowed],
  ];
  it.each(shown)("the page shows %i %s as its plain line", async (status, word, line) => {
    const { api } = net({ [`GET /me/wizard/${G}`]: [{ body: {} }, { body: draft({ step: 1 }) }], [`POST /me/wizard/${G}`]: { status, body: { detail: word } } });
    render(<Wizard api={api} guild={G} />);
    fireEvent.click(await screen.findByRole("button", { name: w.start }));
    expect((await screen.findByRole("alert")).textContent).toBe(line);
  });

  it("every word the finish run can end with has a plain line, and the ones that leave a campaign link to it", () => {
    const words = ["plan", "not-allowed", "gone", "no-key", "failed", "lost", "party_failed", "lore_failed", "docs_left", "bad_answers", "no_rights", "drive_gone"];
    for (const word of words) expect(w.finish.errors[word], word).toBeTruthy();
    expect(w.finish.errors["docs_left"]).toContain("House rules");
  });
});

describe("the finish card", () => {
  it("shows each stage the server reports and ends at the campaign's page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const run = (stage: string, part = 0, of = 0, state = "working", campaign = "") => ({ body: { id: "f1", state, stage, part, ofParts: of, campaign, error: "", createdAt: 100, movedAt: 110, now: 111 } });
    const { api } = net({
      [`GET /me/wizard/${G}`]: { body: draft({ step: 10, answers: { name: "Frostmaiden" }, documents: [] }) },
      [`GET /me/wizard/${G}/finish`]: [{ body: {} }, run("campaign"), run("lore", 1, 2, "working", "c9"), run("clean", 0, 0, "done", "c9")],
      [`POST /me/wizard/${G}/finish`]: { body: { id: "f1" } },
    });
    render(<Wizard api={api} guild={G} />);
    fireEvent.click(await screen.findByRole("button", { name: w.finish.button }));
    await vi.advanceTimersByTimeAsync(2100); // (the first look at the run comes after a moment)
    expect(await screen.findByText(w.finish.stages["campaign"]!)).toBeTruthy();
    for (let i = 0; i < 12 && !screen.queryByText(w.finish.done); i++) await vi.advanceTimersByTimeAsync(2100);
    expect(screen.getByText(w.finish.done)).toBeTruthy();
    expect(screen.getByRole("link", { name: w.finish.open }).getAttribute("href")).toContain(`${G}.c9`);
  });

  it("a finish that ended on the plan offers no retry (a second try cannot help)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed = { body: { id: "f1", state: "failed", stage: "campaign", part: 0, ofParts: 0, campaign: "", error: "plan", createdAt: 100, movedAt: 110, now: 111 } };
    const { api } = net({
      [`GET /me/wizard/${G}`]: { body: draft({ step: 10, answers: { name: "Frostmaiden" }, documents: [] }) },
      [`GET /me/wizard/${G}/finish`]: [{ body: {} }, failed],
      [`POST /me/wizard/${G}/finish`]: { body: { id: "f1" } },
    });
    render(<Wizard api={api} guild={G} />);
    fireEvent.click(await screen.findByRole("button", { name: w.finish.button }));
    await vi.advanceTimersByTimeAsync(2100);
    expect((await screen.findAllByText(w.finish.errors["plan"]!)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: w.finish.retry })).toBeNull();
  });

  it("a campaign made but its documents left shows the campaign link and no retry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed = { body: { id: "f1", state: "failed", stage: "clean", part: 0, ofParts: 0, campaign: "c9", error: "docs_left", createdAt: 100, movedAt: 110, now: 111 } };
    const { api } = net({
      [`GET /me/wizard/${G}`]: { body: draft({ step: 10, answers: { name: "Frostmaiden" }, documents: [] }) },
      [`GET /me/wizard/${G}/finish`]: [{ body: {} }, failed],
      [`POST /me/wizard/${G}/finish`]: { body: { id: "f1" } },
    });
    render(<Wizard api={api} guild={G} />);
    fireEvent.click(await screen.findByRole("button", { name: w.finish.button }));
    await vi.advanceTimersByTimeAsync(2100);
    expect((await screen.findAllByText(w.finish.errors["docs_left"]!)).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: w.finish.open })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.finish.retry })).toBeNull();
  });
});

describe("the finish row's finer counts (#4213)", () => {
  it("reads the pass word and the four numbers, and gives zeros when the server sends none", async () => {
    const { api } = net({ [`GET /me/wizard/${G}/finish`]: [{ body: { state: "working", stage: "lore", pass: "finding_names", passDone: 2, passTotal: 5, foundNames: 12, foundLinks: 3, now: 5 } }, { body: { state: "working", stage: "lore", now: 6 } }] });
    const first = await api.finishState(G);
    expect([first?.pass, first?.passDone, first?.passTotal, first?.foundNames, first?.foundLinks]).toEqual(["finding_names", 2, 5, 12, 3]);
    const second = await api.finishState(G);
    expect([second?.pass, second?.passDone, second?.passTotal, second?.foundNames, second?.foundLinks]).toEqual(["", 0, 0, 0, 0]);
  });
});

describe("a link document (#4206)", () => {
  it("goes with its source and its address as the text, and no file data", async () => {
    const { api, seen } = net({ [`POST /me/wizard/${G}/documents`]: { body: { id: "d7", slot: "lore", name: "https://example.com/notes", pages: 0, words: 0, status: "ok", reason: "" } } });
    const doc = await api.docAdd(G, "lore", { name: "https://example.com/notes", source: "link", link: "https://example.com/notes" });
    expect(doc.id).toBe("d7");
    expect(seen.find((c) => c.key === `POST /me/wizard/${G}/documents`)?.body).toEqual({ slot: "lore", name: "https://example.com/notes", source: "link", text: "https://example.com/notes" });
  });
});

describe("the pictures-only count (#4030)", () => {
  it("docAdd keeps picturePages from the upload's own answer", async () => {
    const { api } = net({ [`POST /me/wizard/${G}/documents`]: { body: { id: "d9", slot: "lore", name: "lore-pictures.pdf", pages: 4, words: 40, status: "ok", reason: "", picturePages: 3 } } });
    const doc = await api.docAdd(G, "lore", { name: "lore-pictures.pdf", source: "upload", data: toBase64(new TextEncoder().encode("hello")) });
    expect(doc.picturePages).toBe(3);
  });

  it("a server that omits the count gives none, not a made-up one", async () => {
    const { api } = net({ [`POST /me/wizard/${G}/documents`]: { body: { id: "d8", slot: "lore", name: "plain.txt", pages: 0, words: 9, status: "ok", reason: "" } } });
    const doc = await api.docAdd(G, "lore", { name: "plain.txt", source: "upload", data: toBase64(new TextEncoder().encode("hello")) });
    expect(doc.picturePages).toBe(0);
  });

  it("a re-read draft's documents have no pictures-only count", async () => {
    const { api } = net({ [`GET /me/wizard/${G}`]: { body: draft({ documents: [{ id: "d1", slot: "lore", name: "lore-pictures.pdf", source: "upload", pages: 4, words: 40, status: "ok", reason: "", picturePages: 3 }] }) } });
    const got = await api.get(G);
    expect(got?.documents[0]?.picturePages).toBeUndefined();
  });
});

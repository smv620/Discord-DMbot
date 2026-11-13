// The start-up wizard's real calls (#4025): the routes core serves at /me/wizard/{guild}/... (web/app.py, #3982). The pages speak
// camelCase and the routes map it themselves, so the bodies here are the page's own shapes. A refusal keeps the server's word
// (`WizardError.kind`), which Wizard.tsx turns into a plain line. The website never holds a document's words: a file goes as base64
// in the body and only its name, pages and words come back. In a file of its own so the other account pages carry none of it.
import { emptyAnswers, WizardError, type WizardApi, type WizardDoc, type WizardDraft, type WizardFinish, type WizardFinishStage } from "./wizard-api";

interface DraftWire {
  step?: number;
  answers?: Record<string, unknown>;
  rightsShared?: boolean;
  rightsRuleset?: boolean;
  expiresAt?: number;
  now?: number;
  documents?: Partial<WizardDoc>[];
}

/** The draft as the routes give it, with every answer the server left out filled in with the page's own default. */
export function draftFrom(wire: DraftWire): WizardDraft {
  const answers = { ...emptyAnswers(), ...(wire.answers ?? {}) } as WizardDraft["answers"];
  if (!Array.isArray(answers.characters)) answers.characters = [];
  if (!Array.isArray(answers.homebrew_links)) answers.homebrew_links = [];
  if (!Array.isArray(answers.skipped)) answers.skipped = [];
  return {
    step: typeof wire.step === "number" ? wire.step : 1,
    answers,
    documents: (wire.documents ?? []).map((d) => ({
      id: String(d.id ?? ""),
      slot: d.slot ?? "lore",
      name: String(d.name ?? ""),
      source: d.source ?? "upload",
      pages: Number(d.pages ?? 0),
      words: Number(d.words ?? 0),
      status: d.status === "unreadable" ? "unreadable" : "ok",
      reason: String(d.reason ?? ""),
    })),
    rights: { shared: wire.rightsShared === true, ruleset: wire.rightsRuleset === true },
    expiresAt: Number(wire.expiresAt ?? 0),
    serverNow: Number(wire.now ?? 0),
  };
}

/** The last step the server stores (its doors take 1 to 9); the finish page after it is not saved. */
const LAST_STORED_STEP = 9;

export function httpWizard(base: string, fetcher: typeof fetch = fetch): WizardApi {
  const root = base.replace(/\/+$/, "");

  async function call(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<unknown> {
    let response: Response;
    try {
      const init: RequestInit = { method, credentials: "include", headers: { "X-DMbot-Request": "1", Accept: "application/json" } };
      if (body !== undefined) {
        init.headers = { ...init.headers, "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      response = await fetcher(`${root}${path}`, init);
    } catch {
      throw new WizardError("network");
    }
    if (response.status === 401) throw new WizardError("signed_out");
    if (!response.ok) {
      let code = "";
      try {
        const found = (await response.json()) as { error?: unknown; detail?: unknown };
        code = String(found.error ?? (typeof found.detail === "string" ? found.detail : ""));
      } catch {
        // no JSON body: a plain server error
      }
      throw new WizardError(code || (response.status === 403 ? "not_allowed" : response.status === 404 ? "not_found" : "server"));
    }
    return response.status === 204 ? null : response.json();
  }

  const at = (guild: string, rest = ""): string => `/me/wizard/${encodeURIComponent(guild)}${rest}`;
  async function draft(guild: string): Promise<WizardDraft> {
    const got = (await call("GET", at(guild))) as DraftWire;
    if (!got || typeof got.step !== "number") throw new WizardError("no_draft");
    return draftFrom(got);
  }

  return {
    async get(guild) {
      const got = (await call("GET", at(guild))) as DraftWire | null;
      return got && typeof got.step === "number" ? draftFrom(got) : null; // ({} is "no draft")
    },
    async start(guild, name) {
      await call("POST", at(guild), { name });
      return draft(guild);
    },
    // The server keeps steps 1 to 9; the last page (10) is the page's own: it is shown from the draft, not stored.
    async set(guild, step, patch) {
      await call("PUT", at(guild, "/step"), { step: Math.min(step, LAST_STORED_STEP), answers: patch });
      const got = await draft(guild);
      return step > LAST_STORED_STEP ? { ...got, step } : got;
    },
    async skip(guild, step) {
      await call("POST", at(guild, "/skip"), { step: Math.min(step, LAST_STORED_STEP) });
      return draft(guild);
    },
    // The public sheet link is not in the first release (#3953): the route is not served yet.
    async sheet() {
      throw new WizardError("not_found");
    },
    async sheetState() {
      throw new WizardError("not_found");
    },
    async pageText(guild, text) {
      const got = (await call("POST", at(guild, "/page-text"), { text })) as { name?: unknown; characters?: unknown };
      const names = Array.isArray(got.characters) ? got.characters.map(String) : [];
      return { name: typeof got.name === "string" ? got.name : "", characters: names.map((full) => ({ full, partyName: full.split(" ")[0] ?? full })) };
    },
    async members(guild) {
      const got = (await call("GET", at(guild, "/members"))) as { members?: unknown };
      return Array.isArray(got.members) ? (got.members as { userId: string; name: string; before: boolean }[]).map((m) => ({ userId: String(m.userId), name: String(m.name), before: m.before === true })) : [];
    },
    async docAdd(guild, slot, input) {
      const body: Record<string, unknown> = { slot, name: input.name };
      // A link document (#4206): its address goes as the text, with the source saying it is to be fetched, not read now.
      if (input.source === "link" && input.link !== undefined) {
        body["source"] = "link";
        body["text"] = input.link;
      } else if (input.data !== undefined) body["data"] = input.data;
      else if (input.text !== undefined) body["text"] = input.text;
      else throw new WizardError("bad_document");
      const got = (await call("POST", at(guild, "/documents"), body)) as Partial<WizardDoc>;
      // The pictures-only count comes from this upload's own answer only (#4030): a re-read draft never has it.
      const doc = draftFrom({ documents: [got] }).documents[0]!;
      return { ...doc, picturePages: typeof got.picturePages === "number" ? got.picturePages : 0 };
    },
    async docRemove(guild, id) {
      await call("DELETE", at(guild, `/documents/${encodeURIComponent(id)}`));
    },
    async rights(guild, kind) {
      await call("POST", at(guild, "/rights"), { kind });
    },
    async finish(guild) {
      return String(((await call("POST", at(guild, "/finish"))) as { id?: unknown }).id ?? "");
    },
    async finishState(guild): Promise<WizardFinish | null> {
      const got = (await call("GET", at(guild, "/finish"))) as Record<string, unknown> | null;
      if (!got || typeof got["state"] !== "string") return null; // ({} is "no finish yet")
      const campaign = typeof got["campaign"] === "string" ? got["campaign"] : "";
      return {
        state: got["state"] as WizardFinish["state"],
        stage: String(got["stage"] ?? "") as WizardFinishStage | "",
        part: Number(got["part"] ?? 0),
        ofParts: Number(got["ofParts"] ?? 0),
        error: String(got["error"] ?? ""),
        pass: String(got["pass"] ?? ""),
        passDone: Number(got["passDone"] ?? 0),
        passTotal: Number(got["passTotal"] ?? 0),
        foundNames: Number(got["foundNames"] ?? 0),
        foundLinks: Number(got["foundLinks"] ?? 0),
        notes: String(got["notes"] ?? ""),
        campaignRef: campaign ? `${guild}.${campaign}` : "",
        dmBotChannel: typeof got["dmBotChannel"] === "string" && /^[0-9]{1,20}$/.test(got["dmBotChannel"]) ? got["dmBotChannel"] : "",
        createdAt: Number(got["createdAt"] ?? 0),
        movedAt: Number(got["movedAt"] ?? 0),
        serverNow: Number(got["now"] ?? 0),
      };
    },
    async discard(guild) {
      await call("DELETE", at(guild));
    },
  };
}

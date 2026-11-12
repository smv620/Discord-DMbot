// The wizard's pretend API (#3956): keeps one draft in memory so every page can be tried and tested without a server. Server ids
// ending in a word change what happens: `.full` has no room for another campaign, `.expired` has no draft, `.fail` makes the
// finish run stop with "failed", `.nokey` stops it with "no-key" (the campaign is made), `.slow` takes longer to finish.
import { SUPPLEMENTS } from "../content/wizard-words";
import { emptyAnswers, WizardError, type WizardApi, type WizardDoc, type WizardDraft, type WizardFinish, type WizardFinishStage, type WizardSlot } from "./wizard-api";

const DAY = 86400;
const STAGES: WizardFinishStage[] = ["campaign", "party", "rules", "house_homebrew", "data_use", "lore", "secrets", "sure", "clean"];
const NOW = (): number => Math.floor(Date.now() / 1000);

export function mockWizard(): WizardApi & { calls: string[] } {
  const calls: string[] = [];
  const drafts = new Map<string, WizardDraft>();
  const finishes = new Map<string, { started: number; looks: number }>();
  let seq = 0;

  const fresh = (name: string): WizardDraft => ({
    step: 1,
    answers: { ...emptyAnswers(), name },
    documents: [],
    rights: { shared: false, ruleset: false },
    expiresAt: NOW() + 7 * DAY,
    serverNow: NOW(),
  });
  const mine = (guild: string): WizardDraft => {
    const draft = drafts.get(guild);
    if (!draft) throw new WizardError("expired");
    draft.expiresAt = NOW() + 7 * DAY;
    draft.serverNow = NOW();
    return draft;
  };
  const copy = (draft: WizardDraft): WizardDraft => JSON.parse(JSON.stringify(draft)) as WizardDraft;

  return {
    calls,
    async get(guild) {
      calls.push(`get:${guild}`);
      if (guild.endsWith(".expired")) return null;
      const draft = drafts.get(guild);
      return draft ? copy(draft) : null;
    },
    async start(guild, name) {
      calls.push(`start:${guild}`);
      if (guild.endsWith(".full")) throw new WizardError("no_room");
      const draft = drafts.get(guild) ?? fresh(name);
      drafts.set(guild, draft);
      return copy(draft);
    },
    async set(guild, step, patch) {
      calls.push(`set:${guild}:${step}:${Object.keys(patch).join(",")}`);
      const draft = mine(guild);
      if ((patch.target ?? draft.answers.target) === (patch.fallback ?? draft.answers.fallback)) throw new WizardError("bad_value", "fallback");
      if (patch.invite && !/^https:\/\/www\.dndbeyond\.com\/campaigns\/join\/\d+/.test(patch.invite)) throw new WizardError("bad_value", "invite");
      if (patch.supplements?.some((code) => !SUPPLEMENTS.some((s) => s.code === code))) throw new WizardError("bad_value", "supplements");
      if ((patch.characters?.length ?? 0) > 12) throw new WizardError("bad_value", "characters");
      draft.answers = { ...draft.answers, ...patch };
      draft.step = step;
      return copy(draft);
    },
    async skip(guild, step) {
      calls.push(`skip:${guild}:${step}`);
      const draft = mine(guild);
      if (!draft.answers.skipped.includes(step)) draft.answers.skipped.push(step);
      return copy(draft);
    },
    async sheet(guild, url) {
      calls.push(`sheet:${guild}`);
      mine(guild);
      seq += 1;
      return `sheet${seq}:${url}`;
    },
    async sheetState(guild, id) {
      calls.push(`sheetState:${guild}`);
      const url = id.slice(id.indexOf(":") + 1);
      if (url.includes("private")) return { state: "failed", name: "", error: "private" };
      if (url.includes("missing")) return { state: "failed", name: "", error: "not_found" };
      return { state: "done", name: url.includes("ivy") ? "Ivy Quickfoot" : "Brannoc Stonebelt", error: "" };
    },
    async pageText(guild, text) {
      calls.push(`pageText:${guild}`);
      const draft = mine(guild);
      draft.answers.pageTextUsed = true;
      if (!text.includes("Brannoc")) return { name: "", characters: [] };
      return { name: "The Frostmaiden's Winter", characters: [{ full: "Brannoc Stonebelt", partyName: "Brannoc" }, { full: "Ivy Quickfoot", partyName: "Ivy" }] };
    },
    async docAdd(guild, slot, input) {
      calls.push(`docAdd:${guild}:${slot}`);
      const draft = mine(guild);
      if (draft.documents.length >= 20) throw new WizardError("too_many");
      if (slot !== "ruleset" && slot !== "lore" && slot !== "secrets" && slot !== "house" && slot !== "homebrew") throw new WizardError("bad_value", "slot");
      seq += 1;
      const text = input.text ?? (input.data !== undefined ? atob(input.data) : "");
      const words = text.split(/\s+/).filter(Boolean).length;
      const scanned = input.name.includes("scan");
      const doc: WizardDoc = {
        id: `doc${seq}`,
        slot: slot as WizardSlot,
        name: input.name,
        source: input.source,
        pages: input.source === "upload" && input.name.endsWith(".pdf") ? 3 : 0,
        words: input.source === "upload" ? words : 0,
        status: scanned || (input.source === "upload" && words === 0) ? "unreadable" : "ok",
        reason: scanned ? "scanned" : input.source === "upload" && words === 0 ? "empty" : "",
      };
      // The pretend server answers how many pages are pictures only, as the real one does for a name that says so (#4030).
      const picturePages = input.name.includes("onepicture") ? 1 : input.name.includes("pictures") ? 3 : 0;
      draft.documents.push(doc); // (kept on the draft without the count, as the server keeps it)
      return { ...doc, picturePages };
    },
    async docRemove(guild, id) {
      calls.push(`docRemove:${guild}:${id}`);
      const draft = mine(guild);
      draft.documents = draft.documents.filter((d) => d.id !== id);
    },
    async rights(guild, kind) {
      calls.push(`rights:${guild}:${kind}`);
      mine(guild).rights[kind] = true;
    },
    async finish(guild) {
      calls.push(`finish:${guild}`);
      const draft = mine(guild);
      if (!draft.answers.name.trim()) throw new WizardError("bad_value", "name");
      const needsRights = draft.documents.some((d) => d.slot !== "ruleset");
      if (needsRights && !draft.rights.shared) throw new WizardError("rights_needed");
      if (draft.documents.some((d) => d.slot === "ruleset") && !draft.rights.ruleset) throw new WizardError("rights_needed");
      seq += 1;
      finishes.set(guild, { started: NOW(), looks: 0 });
      return `fin${seq}`;
    },
    async finishState(guild): Promise<WizardFinish | null> {
      calls.push(`finishState:${guild}`);
      const run = finishes.get(guild);
      if (!run) return null;
      run.looks += 1;
      const per = guild.endsWith(".slow") ? 3 : 1; // looks for each stage
      const at = Math.floor((run.looks - 1) / per);
      const now = NOW();
      const base = { createdAt: run.started, movedAt: now, serverNow: now, part: 0, ofParts: 0, pass: "", passDone: 0, passTotal: 0, foundNames: 0, foundLinks: 0 };
      if (guild.endsWith(".fail") && at >= 2) return { ...base, state: "failed", stage: STAGES[2]!, error: "failed", campaignRef: "" };
      if (guild.endsWith(".nokey") && at >= 5) return { ...base, state: "failed", stage: "lore", error: "no-key", campaignRef: `${guild}.cmp-new` };
      if (at >= STAGES.length) {
        drafts.delete(guild);
        return { ...base, state: "done", stage: "clean", error: "", campaignRef: `${guild}.cmp-new` };
      }
      const stage = STAGES[at]!;
      const docs = stage === "lore" || stage === "secrets" ? (drafts.get(guild)?.documents.filter((d) => d.slot === stage).length ?? 0) : 0;
      const counts = guild.endsWith(".counts") && stage === "lore" ? { pass: "finding_names", passDone: 2, passTotal: 5, foundNames: 12, foundLinks: 1 } : {};
      return { ...base, ...counts, state: "working", stage, part: docs ? 1 : 0, ofParts: docs, error: "", campaignRef: at > 0 ? `${guild}.cmp-new` : "" };
    },
    // This server's members (#4204): those who played with the DM before come first, then by name; ".nomembers" has none, ".brokenmembers" fails.
    async members(guild) {
      calls.push(`members:${guild}`);
      if (guild.endsWith(".brokenmembers")) throw new WizardError("server");
      if (guild.endsWith(".nomembers")) return [];
      return [
        { userId: "1001", name: "Zoe", before: true },
        { userId: "1002", name: "Ada", before: false },
        { userId: "1003", name: "Ben", before: false },
        { userId: "1004", name: "Eric the Red", before: false },
      ];
    },
    async discard(guild) {
      calls.push(`discard:${guild}`);
      drafts.delete(guild);
      finishes.delete(guild);
    },
  };
}

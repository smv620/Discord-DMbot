// The "names" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `NAMES_METHODS` there, and a test keeps the two in step.
import type {
  CampaignName,
  ConfirmAnswer,
  MyNpcNote,
  NameAnswer,
  NameAnswerResult,
  IconChoice,
  ClarifyName,
  NameCheckList,
  NamesFixed,
  CleanupView,
  NamePlace,
  NameSource,
  KindOffer,
  MatchRow,
  PrivateScene,
  NamesPage,
  PlaceSpelling,
  ToldName,
  NpcPage,
  CampaignNameChecks,
  QuestionCounts,
  Readings,
  SmartNamesAnswer,
  Waiting,
  WebGraph,
  WebViewers,
  WebOverview,
} from "./api";
import {
  ApiError,
  NAME_POLLS,
  NAME_POLL_MS,
  READING_POLLS,
  errorKinds,
} from "./api";
import { countsOf, sharedOf } from "./api";
import type { AccountApi, ApiHelpers, HandOverQuestion, NodeSummary, TraitOffer, UploadNotice, WaitingLook } from "./api";

/** One number from the bot's `counts` text ("shown:1", "kept:3 shown:2"), only when it is above 0: a count of 0 is not said (#3974, #3986). */
export function countOf(counts: unknown, key: string): number | undefined {
  if (typeof counts !== "string") return undefined;
  for (const [, name, digits] of counts.matchAll(/(?<![a-z_])([a-z_]+):(\d+)/g)) {
    if (name === key) {
      const n = Number(digits);
      return n > 0 ? n : undefined;
    }
  }
  return undefined;
}

export default function namesArea({ call, waitForAnswer }: ApiHelpers): Partial<AccountApi> {
  return {
    namesFixed: async (recapRef) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(recapRef)}/names-fixed`)) as NamesFixed,
    undoNameFix: async (recapRef, id, onLook) => {
      const queued = (await call("POST", `/me/recaps/${encodeURIComponent(recapRef)}/names-fixed/${encodeURIComponent(id)}/undo`, {})) as {
        answer: string;
      };
      if (!queued?.answer) throw new ApiError("server");
      // The bot's answer is polled under the campaign, as the report's are (the campaign is the recap's first two parts).
      const campaign = recapRef.split(".").slice(0, 2).join(".");
      const got = await waitForAnswer(`/me/campaigns/${encodeURIComponent(campaign)}/web/answers/${encodeURIComponent(queued.answer)}`, (look) =>
        onLook?.(sharedOf(look.progress)),
      );
      return { putBack: got.putBack ?? 0 };
    },
    campaignNames: async (campaignRef) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/names`,
        )) as { names: CampaignName[] }
      ).names,
    suggestSaidAs: async (campaignRef, nameId) =>
      (
        (await call(
          "POST",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as/suggest`,
        )) as { suggestion: string }
      ).suggestion,
    // The press is queued for the bot (the web container never holds the speech key), then polled about once a second
    // for up to a minute; the clip arrives once, as base64 WAV, on the look that says done.
    hearSaidAs: async (campaignRef, nameId, text, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as/hear`;
      const queued = (await call("POST", base, { text })) as { id: string };
      if (!queued?.id) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        let got = null as { state?: string; error?: string; progress?: unknown; audio?: string; mime?: string } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(queued.id)}`)) as NonNullable<typeof got>;
          onLook?.(sharedOf(got?.progress as never));
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "signed-out" || error.kind === "not-found" || error.kind === "not-allowed")) throw error;
        }
        if (got?.state === "done") {
          if (!got.audio) throw new ApiError("server");
          const bytes = Uint8Array.from(atob(got.audio), (c) => c.charCodeAt(0));
          return new Blob([bytes], { type: got.mime ?? "audio/wav" });
        }
        if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    // The bot listens, not this server: a clip it answers within ~20 s comes back at once (200); otherwise a 202 {id} is
    // polled every 1.5 s for up to 3 minutes (#3828, core #3827). The page can stop waiting at any look.
    respellFromVoice: async (campaignRef, nameId, clip, watch) => {
      const base = `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as/from-voice`;
      const first = (await call("POST", base, undefined, false, clip)) as { respelling?: string; id?: string };
      if (first?.respelling) return first.respelling;
      if (!first?.id) throw new ApiError("server");
      let misses = 0;
      for (let i = 0; i < 120; i++) {
        if (watch?.cancelled?.()) throw new ApiError("server");
        await new Promise((r) => setTimeout(r, 1500));
        if (watch?.cancelled?.()) throw new ApiError("server");
        let got = null as { state?: string; error?: string; respelling?: string; progress?: unknown } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(first.id)}`)) as NonNullable<typeof got>;
          misses = 0;
        } catch (error) {
          if (error instanceof ApiError && ["signed-out", "not-found", "not-allowed", "bad-clip", "voice-off", "too-soon", "too-big"].includes(error.kind)) throw error;
          // A server that stays down says so after a few looks, not after three silent minutes.
          if ((misses += 1) >= 5) throw new ApiError("server");
        }
        if (got?.state === "done") {
          if (!got.respelling) throw new ApiError("server");
          return got.respelling;
        }
        if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        if (got?.state === "waiting" || got?.state === "working") watch?.onLook?.({ state: got.state, progress: sharedOf(got.progress as never) });
      }
      throw new ApiError("still-waiting");
    },
    saveSaidAs: async (campaignRef, nameId, saidAs) => {
      await call(
        "PUT",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as`,
        { saidAs },
      );
    },
    nameChecks: async (ref) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/names-to-check`)) as NameCheckList,
    // One name on its own page (#3604): the same address as its answer, read with GET; the core door comes with #3596/#3602.
    clarifyName: async (ref, id) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/names-to-check/${encodeURIComponent(id)}`)) as ClarifyName,
    answerNameCheck: async (ref, id, answer) => {
      await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/names-to-check/${encodeURIComponent(id)}`,
        answer,
      );
    },
    nameAnswerResult: async (ref, id) =>
      (await call(
        "GET",
        `/me/recaps/${encodeURIComponent(ref)}/names-to-check/${encodeURIComponent(id)}/answer`,
      )) as NameAnswerResult,
    npcPage: async (ref, entry) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/npcs/${encodeURIComponent(entry)}`,
      )) as NpcPage,
    saveNpcNote: async (ref, entry, body) => {
      await call(
        "PUT",
        `/me/campaigns/${encodeURIComponent(ref)}/npcs/${encodeURIComponent(entry)}/note`,
        { body },
      );
    },
    myNpcNotes: async (ref) =>
      ((await call("GET", `/me/npc-notes?campaign=${encodeURIComponent(ref)}`)) as {
        notes: MyNpcNote[];
      }).notes,
    deleteMyNpcNotes: async (ref) =>
      ((await call("DELETE", `/me/npc-notes?campaign=${encodeURIComponent(ref)}`)) as {
        deleted: number;
      }).deleted,
    allNames: async (ref, q) => {
      const params = new URLSearchParams({ q: q.q, kind: q.kind, sort: q.sort, page: String(q.page) });
      if (q.size !== undefined) params.set("size", String(q.size));
      if (q.unnamed) params.set("unnamed", "1");
      if (q.sources?.length) params.set("sources", q.sources.join(","));
      return (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/all-names?${params.toString()}`,
      )) as NamesPage;
    },
    graphCleanup: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/cleanup`)) as CleanupView,
    cleanupPlaces: async (ref, id) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/cleanup/${encodeURIComponent(id)}/places`)) as { places: NamePlace[]; morePlaces: number },
    nameSources: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/name-sources`)) as { sources: NameSource[] }).sources,
    nameMatches: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/all-names-matches`)) as { matches: MatchRow[] }).matches,
    // Queued for the bot like every name action (202 {answer}); the answer is read where every all-names answer is.
    answerMatch: async (ref, batch, answer, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/all-names-matches/${encodeURIComponent(batch)}/${answer}`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/all-names/answers/${encodeURIComponent(queued.answer)}`, (look) =>
        onLook?.({ waits: look.waits ?? null, progress: countsOf(look.progress) }),
      );
    },
    // "Private scenes" (#4102): the list, and "Not private", which the door does itself (no queue: it is one flag on one row).
    privateScenes: async (ref) => ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/private-scenes`)) as { items: PrivateScene[] }).items,
    notPrivateScene: async (ref, id) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/private-scenes/${encodeURIComponent(id)}/not-private`, {})) as { first: string; second: string },
    smartNames: async (ref, words) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/all-names/search`, { q: words })) as SmartNamesAnswer,
    // The bot applies a name change, so the POST only queues it (202 {answer}); this waits for the
    // answer by asking about once a second, up to a minute (#2294).
    changeName: async (ref, id, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/all-names`;
      const queued = (await call("POST", `${base}/${encodeURIComponent(id)}`, change)) as {
        answer: string;
      };
      if (!queued?.answer) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        // Once queued, the bot may still apply the change, so a failed look (a blip, a 5xx, a 404 on
        // the answer) is not an answer: keep asking until the minute is up. Only "signed out" stops.
        let got: NameAnswer | null = null;
        try {
          got = (await call(
            "GET",
            `${base}/answers/${encodeURIComponent(queued.answer)}`,
          )) as NameAnswer;
        } catch (error) {
          if (error instanceof ApiError && error.kind === "signed-out") throw error;
        }
        if (got?.state === "done") return got.removed || !got.row ? { removed: true } : got.row;
        if (got?.state === "failed") {
          throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        }
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      // The bot may still apply it later: the page says so and reads the list again, never "nothing
      // was changed", so nobody tries a merge or remove twice.
      throw new ApiError("still-waiting");
    },
    placeSpellings: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/place-spellings`)) as {
        spellings: PlaceSpelling[];
      }).spellings,
    // Queued like any name change (the bot confirms or turns down the proposed name), then waited for.
    answerPlaceSpelling: async (ref, spelling, yes) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/all-names`;
      const queued = (await call("POST", `${base}/${encodeURIComponent(spelling.placeId)}`, {
        action: yes ? "spelling-yes" : "spelling-no",
        alias: spelling.id,
      })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        let got: NameAnswer | null = null;
        try {
          got = (await call("GET", `${base}/answers/${encodeURIComponent(queued.answer)}`)) as NameAnswer;
        } catch (error) {
          if (error instanceof ApiError && error.kind === "signed-out") throw error;
        }
        if (got?.state === "done") return;
        if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    campaignKindOffers: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/kind-offers`)) as { offers: KindOffer[] }).offers,
    // Queued like any name change (the bot lifts the answer), then waited for.
    liftKindOffer: async (ref, words) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/all-names-confirm`, { action: "offer-clear", words })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        let got: NameAnswer | null = null;
        try {
          got = (await call("GET", `${base}/all-names/answers/${encodeURIComponent(queued.answer)}`)) as NameAnswer;
        } catch (error) {
          if (error instanceof ApiError && error.kind === "signed-out") throw error;
        }
        if (got?.state === "done") return;
        if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    toldPanel: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/told-panel`)) as { names: ToldName[] }).names,
    nameThem: async (ref, id, step, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/all-names`;
      const queued = (await call("POST", `${base}/${encodeURIComponent(id)}`, step)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        let got: NameAnswer | null = null;
        try {
          got = (await call("GET", `${base}/answers/${encodeURIComponent(queued.answer)}`)) as NameAnswer;
          onLook?.();
        } catch (error) {
          if (error instanceof ApiError && error.kind === "signed-out") throw error;
        }
        if (got?.state === "done") {
          if (got.player) return { state: "player", ...got.player };
          return { state: "done", row: got.row ?? null, undo: got.undo ? queued.answer : null, answer: queued.answer };
        }
        if (got?.state === "failed") {
          if (got.error === "known_name" && got.other) return { state: "question", other: got.other };
          throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        }
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    webGraph: async (ref, q) => {
      const params = new URLSearchParams({ hops: String(q.hops) });
      if (q.center) params.set("center", q.center);
      if (q.seeAs) {
        // The web as one character (or the party) knows it (#2999): the same shape, plus whose it is.
        params.set("viewer", q.seeAs);
        return (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/see-as?${params.toString()}`)) as WebGraph;
      }
      if (q.view) params.set("view", q.view);
      return (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/web?${params.toString()}`,
      )) as WebGraph;
    },
    webVersion: async (ref) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/version`)) as { version: number },
    webSize: async (ref, q) => {
      const params = new URLSearchParams({ hops: String(q.hops) });
      if (q.center) params.set("center", q.center);
      // The size is of the web in the view being asked for, so a DM in Players View or a player
      // peeking must not be told the size of the other one.
      if (q.view) params.set("view", q.view);
      return (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/web/size?${params.toString()}`,
      )) as { names: number };
    },
    webOverview: async (ref, q) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/overview${q.view ? `?view=${q.view}` : ""}`)) as WebOverview,
    webFind: async (ref, q) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/find?view=${q.view ?? ""}&q=${encodeURIComponent(q.q)}`)) as { names: { id: string; name: string; kind: string }[] }).names,
    webNodeSummary: async (ref, id, peek) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/nodes/${encodeURIComponent(id)}/summary${peek ? "?peek=1" : ""}`)) as NodeSummary,
    confirmKinds: async (ref, upload, kinds) => {
      const queued = (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/all-names-confirm`, {
        action: "confirm-kinds",
        upload,
        kinds,
      })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      return queued;
    },
    // What DMbot added by itself after one upload (#3879, core #3887): counts and the label only.
    uploadNotice: async (ref, source) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/upload-notice?kind=${source.kind}&id=${encodeURIComponent(source.id)}`)) as UploadNotice,
    // Undo this upload is queued for the bot, then polled like any queued change: once, within 7 days, newest first.
    undoUpload: async (ref, source, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/upload-notice/undo`;
      const queued = (await call("POST", base, { kind: source.kind, id: source.id })) as { id: string };
      if (!queued?.id) throw new ApiError("server");
      for (let i = 0; i < NAME_POLLS; i++) {
        let got = null as { state?: string; error?: string; undone?: number; progress?: unknown } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(queued.id)}`)) as NonNullable<typeof got>;
          onLook?.(sharedOf(got?.progress as never));
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "signed-out" || error.kind === "not-found" || error.kind === "not-allowed")) throw error;
        }
        if (got?.state === "done") return { took: Number(got.undone ?? 0) };
        if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    confirmUndo: async (ref, answer) => {
      const queued = (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/all-names-confirm`, {
        action: "confirm-undo",
        answer,
      })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      return queued;
    },
    // The bot's answers to "Add all" are polled where every all-names answer is (app.py, all-names/answers).
    confirmAnswer: async (ref, answer) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/all-names/answers/${encodeURIComponent(answer)}`)) as ConfirmAnswer,
    webViewers: async (ref, q) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/viewers?q=${encodeURIComponent(q)}`)) as WebViewers,
    webWaiting: async (ref) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/waiting`)) as Waiting,
    // The answer is queued for the bot (202 {answer}) like every other edit; this waits for it.
    answerWaiting: async (ref, answer, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/web`;
      const body =
        answer.action === "keep_all"
          ? { kind: answer.kind, action: answer.action, ids: answer.ids }
          : answer;
      const queued = (await call("POST", `${base}/waiting`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/answers/${encodeURIComponent(queued.answer)}`, (look) =>
        onLook?.({ waits: look.waits ?? null, progress: countsOf(look.progress) }),
      );
      const kept = (got as { kept?: unknown }).kept;
      const batch = (got as { batch?: unknown }).batch; // the change an answer made, for its Undo (#3645)
      // Whether the bot showed the connection to the players, or kept it hidden (#3974, #3986); only a count above 0 is said.
      const counts = (got as { counts?: unknown }).counts;
      const shown = countOf(counts, "shown");
      const held = countOf(counts, "held");
      // Whether the bot kept it for the characters who were there, or hid it because the party was split (#4035).
      const scene = countOf(counts, "scene");
      const split = countOf(counts, "split");
      return {
        ...(typeof kept === "number" ? { kept } : {}),
        ...(typeof batch === "number" ? { batch } : {}),
        ...(shown !== undefined ? { shown } : {}),
        ...(held !== undefined ? { held } : {}),
        ...(scene !== undefined ? { scene } : {}),
        ...(split !== undefined ? { split } : {}),
      };
    },
    // The hand-over question (#4122): the list, then one queued answer polled here so the bot's own word for how it ended reaches the card.
    handOverQuestions: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/hand-over-questions`)) as { questions: HandOverQuestion[] }).questions,
    answerHandOver: async (ref, question, answer, undoes, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/hand-over-questions/answer`;
      const body: Record<string, string> = { olderTie: question.olderTie, newerTie: question.newerTie, answer };
      if (undoes) body["undoes"] = undoes;
      const queued = (await call("POST", base, body)) as { id: string };
      if (!queued?.id) throw new ApiError("server");
      let misses = 0;
      for (let i = 0; i < NAME_POLLS; i++) {
        let got = null as { state?: string; error?: string; undoable?: boolean; waits?: WaitingLook["waits"]; progress?: Parameters<typeof countsOf>[0] } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(queued.id)}`)) as NonNullable<typeof got>;
          misses = 0;
          onLook?.({ waits: got.waits ?? null, progress: countsOf(got.progress) });
        } catch (error) {
          if (error instanceof ApiError && ["signed-out", "not-found", "not-allowed"].includes(error.kind)) throw error;
          if ((misses += 1) >= 5) throw new ApiError("server");
        }
        if (got?.state === "done") return { answerId: queued.id, undoable: got.undoable === true };
        if (got?.state === "failed") {
          const word = got.error ?? "";
          const failure = new ApiError(word === "gone" ? "gone" : word === "not-allowed" ? "not-allowed" : "server");
          failure.note = word;
          throw failure;
        }
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    // The lineage and class questions (#3946): the list, then one queued answer that is polled like every other edit.
    traitOffers: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/trait-offers`)) as { offers: TraitOffer[] }).offers,
    answerTrait: async (ref, offer, how, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/trait-offers/answer`;
      const queued = (await call("POST", base, { entity: offer.entity, kind: offer.kind, key: offer.key, answer: how })) as { id: string };
      if (!queued?.id) throw new ApiError("server");
      // Polled here (not by `waitForAnswer`) so the bot's own word for a failure reaches the card: `gone`, `refused`, `not-allowed`, `failed`
      // (or the door's `lost`) each have their own plain line. The word is in `note`; the card reads it, no page prints it.
      let misses = 0;
      for (let i = 0; i < NAME_POLLS; i++) {
        let got = null as { state?: string; error?: string; batch?: unknown; waits?: WaitingLook["waits"]; progress?: Parameters<typeof countsOf>[0] } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(queued.id)}`)) as NonNullable<typeof got>;
          misses = 0;
          onLook?.({ waits: got.waits ?? null, progress: countsOf(got.progress) });
        } catch (error) {
          if (error instanceof ApiError && ["signed-out", "not-found", "not-allowed"].includes(error.kind)) throw error;
          if ((misses += 1) >= 5) throw new ApiError("server");
        }
        if (got?.state === "done") return typeof got.batch === "number" ? { batch: got.batch } : {};
        if (got?.state === "failed") {
          const word = got.error ?? "";
          const failure = new ApiError(word === "gone" ? "gone" : word === "not-allowed" ? "not-allowed" : "server");
          failure.note = word;
          throw failure;
        }
        if (i < NAME_POLLS - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
      }
      throw new ApiError("still-waiting");
    },
    webReadings: async (ref) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/readings`)) as Readings,
    answerReading: async (ref, answer, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/web`;
      const queued = (await call("POST", `${base}/readings`, answer)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      // Looks for 150 seconds, longer than the 120 the bot is given before an answer it picked up is taken
      // again, so a wait that has stalled is shown as stalled before the page stops looking.
      const got = await waitForAnswer(
        `${base}/answers/${encodeURIComponent(queued.answer)}`,
        (look) => onLook?.({ waits: look.waits ?? null }),
        READING_POLLS,
      );
      return { answerId: queued.answer, undoable: got.undoable === true };
    },
    campaignNameChecks: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/names-to-check`)) as CampaignNameChecks,
    myQuestions: async () => (await call("GET", "/me/questions")) as QuestionCounts,
    questionsTotal: async () => (await call("GET", "/me/questions/count")) as { total: number },
    webPeek: async (ref) => {
      await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/web/peek`, {});
    },
    // A DM's reveal is queued for the bot like every other edit (202 {answer}); a name is answered at
    // the All names answers, a connection at the web's own (#2405).
    webReveal: async (ref, target) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/web/reveal`, target)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const where = target.kind === "name" ? "all-names" : "web";
      await waitForAnswer(`${base}/${where}/answers/${encodeURIComponent(queued.answer)}`);
    },
    // The connection edits are queued too (202 {answer}); this waits for the edge as it now stands.
    // The icon choice is instant: the door answers with what it was and what it is now (#3621).
    setPcIcon: async (ref, entityId, choice) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/pc-icons/${encodeURIComponent(entityId)}`, { choice })) as { previous: IconChoice; now: IconChoice },
    changeWebLink: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/web`;
      let queued: { answer: string };
      if (change.action === "add") {
        const { action: _action, ...body } = change;
        queued = (await call("POST", `${base}/links`, body)) as { answer: string };
      } else {
        const { id, ...body } = change;
        queued = (await call("POST", `${base}/links/${encodeURIComponent(id)}`, body)) as {
          answer: string;
        };
      }
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/answers/${encodeURIComponent(queued.answer)}`);
      // The change's batch rides along only when the bot made one (an edit that changed nothing has none).
      const made = typeof got.batch === "number" ? { batch: got.batch } : {};
      return got.removed || !got.edge ? { removed: true, ...made } : { ...got.edge, ...made };
    },
  };
}

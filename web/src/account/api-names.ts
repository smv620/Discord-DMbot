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
  MetItem,
  PlaceWorld,
  PlaceWorldTable,
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
  SIGNED_OUT_EVENT,
} from "./api";
import { countsOf, sharedOf } from "./api";
import type { AccountApi, ApiHelpers, CharacterLanguages, ContinuityHits, DeedQuestions, HeardQuestions, LookAlikes, JoinChoices, JoinGroup, PartyRecordCharacter, WebAnswer, ThreadGuesses, HandOverQuestion, MapFeatures, MapsRead, MapsResult, NodeSummary, PartyCharacter, NodeTrait, PlotThreads, RevealProposal, SecretQuestion, StoryThreads, ThreadCard, ThreadClues, ThreadProgressThread, TraitOffer, UploadNotice, WaitingLook, WorldNotes, LeaveGroup } from "./api";

/** One number from the bot's `counts` text ("shown:1", "kept:3 shown:2"), only when it is above 0: a count of 0 is not said (#3974, #3986). */
/** The revealed list of an upload notice (#4661): plain lines of at most 200 characters, at most ten; anything else is dropped. */
export function keepRevealed(raw: unknown): { revealedLines?: string[]; revealedMore?: number } {
  const r = (raw ?? {}) as { revealedLines?: unknown; revealedMore?: unknown };
  const out: { revealedLines?: string[]; revealedMore?: number } = {};
  if (Array.isArray(r.revealedLines)) {
    out.revealedLines = r.revealedLines
      .filter((l): l is string => typeof l === "string" && l.length > 0 && l.length <= 200 && !/[<>\u0000-\u001f]/.test(l))
      .slice(0, 10);
  }
  if (typeof r.revealedMore === "number" && Number.isInteger(r.revealedMore) && r.revealedMore >= 0 && r.revealedMore <= 9999) {
    out.revealedMore = r.revealedMore;
  }
  return out;
}

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

export default function namesArea({ call, waitForAnswer, root }: ApiHelpers): Partial<AccountApi> {
  // The party record's door answers with {answer}; the answer is waited for like the other queued changes (#4778 a2).
  const partyAnswer = async (ref: string, queued: unknown): Promise<WebAnswer> => {
    const answer = (queued as { answer?: string } | null)?.answer;
    if (!answer) throw new ApiError("server");
    return waitForAnswer(`/me/campaigns/${encodeURIComponent(ref)}/web/answers/${encodeURIComponent(answer)}`);
  };
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
    // The press is queued for the bot like the other queued changes; the answer carries how many names were made and how many wait (#5081).
    moreNames: async (campaignRef, onLook) => {
      const queued = (await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/more-names`, {})) as { answer?: string } | null;
      if (!queued?.answer) throw new ApiError("server");
      // Core sends `progress: {done, total}` with no stage for this one, so `sharedOf` would drop it: read the counts.
      const got = await waitForAnswer(`/me/campaigns/${encodeURIComponent(campaignRef)}/web/answers/${encodeURIComponent(queued.answer)}`, (look) => {
        const p = look.progress as { done?: unknown; total?: unknown } | null | undefined;
        onLook?.(p && typeof p.done === "number" && typeof p.total === "number" && p.total > 0 ? { done: p.done, total: p.total } : null);
      });
      // A done answer without its counts is not a success to report ("No more names are waiting" would be false).
      if (typeof got.made !== "number" || typeof got.left !== "number") throw new ApiError("server");
      return { made: got.made, left: got.left };
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
    // "Met but not named" (#4084): the list, and each answer queued for the bot (202 {answer}) and read where every name action's is.
    metList: async (ref) => ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/met`)) as { items: MetItem[] }).items,
    answerMet: async (ref, id, answer, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/met/${encodeURIComponent(id)}`, answer)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      // (the bot's word "met_waiting" has no kind in the shared map, which would cost the first download bytes: it is read off the answer here)
      let word = "";
      try {
        await waitForAnswer(`${base}/all-names/answers/${encodeURIComponent(queued.answer)}`, (look) => {
          word = look.error ?? "";
          onLook?.({ waits: look.waits ?? null, progress: countsOf(look.progress) });
        });
      } catch (error) {
        if (word === "met_waiting") throw new ApiError("met-waiting");
        throw error;
      }
    },
    // "Private scenes" (#4102): the list, and "Not private", which the door does itself (no queue: it is one flag on one row).
    placeWorldTable: async (ref, placeId) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/names/${encodeURIComponent(placeId)}/world/table`)) as PlaceWorldTable,
    placeWorld: async (ref, placeId) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/names/${encodeURIComponent(placeId)}/world`)) as PlaceWorld,
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
      { const raw = (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/upload-notice?kind=${source.kind}&id=${encodeURIComponent(source.id)}`)) as UploadNotice; return { ...raw, ...keepRevealed(raw) }; },
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
    worldNotes: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/world-notes`)) as WorldNotes,
    // The quest sheet of a kept story thread (#4547): read only, DM only (the door answers anyone else with nothing).
    // Queued for the bot like the other website presses (202 {answer}); this waits for it and gives back the batch for Undo (#4562).
    changeThreadParts: async (ref, thread, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/threads/${encodeURIComponent(thread)}/change`, change)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // A character's languages (#4621): read only, DM only; a place or a stranger gets nothing.
    characterLanguages: async (ref, entity) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/names/${encodeURIComponent(entity)}/languages`)) as CharacterLanguages,
    // Queued for the bot (202 {answer}); this waits for it and gives back the batch of the change, for Undo (#4621).
    changeCharacterLanguages: async (ref, entity, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/names/${encodeURIComponent(entity)}`;
      const body = change.action === "undo" ? { action: "undo", batch: String(change.batch) } : change;
      const queued = (await call("POST", `${base}/languages/change`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`/me/campaigns/${encodeURIComponent(ref)}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // The 🧵 Progress to check lines (#4616): DM only; the door answers anyone else with nothing.
    threadProgress: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/thread-progress`)) as { threads: ThreadProgressThread[] }).threads,
    // Queued for the bot (202 {answer}); this waits for it and gives back the batch of a keep, for Undo (#4616).
    changeThreadProgress: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const body = change.action === "undo" && "batch" in change ? { action: "undo", batch: String(change.batch) } : change;
      const queued = (await call("POST", `${base}/thread-progress/change`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // The 🤫 Known or secret? questions, oldest first (#4598): the DM's own; the door answers anyone else with nothing.
    secretQuestions: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/secret-questions`)) as { questions: SecretQuestion[] }).questions,
    // The join questions waiting for the DM (#4778 part b): DM only; the door answers anyone else with nothing.
    // The "Did they mean…?" questions (#4857): read as they are; the three answers are queued for the bot and this waits for each one.
    // 🔗 Look-alikes (#5259): the read, then three queued answers waited for like the other queued presses.
    lookAlikes: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/look-alikes`)) as LookAlikes,
    mergeLookAlikes: async (ref, body, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/look-alikes/merge`, body)) as { answer?: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`, (look) => onLook?.(sharedOf(look.progress)));
      return { batch: got.batch ?? null, secret: got.secret ?? null };
    },
    lookAlikesApart: async (ref, ids) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/look-alikes/apart`, { ids })) as { answer?: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    undoLookAlikes: async (ref, batch) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/look-alikes/undo`, { batch })) as { answer?: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    heardQuestions: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/heard-questions`)) as HeardQuestions,
    heardPick: async (ref, question, index) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/heard-questions/pick`, { question, index })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { code: got.code ?? null };
    },
    heardKeep: async (ref, question) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/heard-questions/keep`, { question })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { code: got.code ?? null };
    },
    // "Type it…" (#4865): a name the DM typed for a waiting question. The bot checks it and answers; a refusal carries its reason.
    heardType: async (ref, question, name) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/heard-questions/type`, { question, name })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      let reason = null as string | null;
      try {
        const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`, (look) => {
          reason = look.reason ?? null;
        });
        return { code: got.code ?? null };
      } catch (error) {
        if (error instanceof ApiError && error.kind === "typed-problem" && reason !== null) error.reason = reason;
        throw error;
      }
    },
    // "Let me choose" (#4891): the choices are read; asking the bot is queued and waited for; a pick is queued and waited for.
    joinChoices: async (ref, group) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/join-questions/${encodeURIComponent(group)}/choices`)) as JoinChoices,
    askJoinChoices: async (ref, group) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/join-questions/choices`, { group })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    joinChoose: async (ref, group, pick) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const body = "session" in pick ? { group, session: pick.session } : { group, thread: pick.thread };
      const queued = (await call("POST", `${base}/join-questions/choose`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return {
        chose: Array.isArray(got.chose) ? got.chose : [],
        choseMore: typeof got.choseMore === "number" ? got.choseMore : 0,
        first: got.first === true,
        batch: typeof got.batch === "number" ? got.batch : null,
      };
    },
    joinQuestions: async (ref) => ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/join-questions`)) as { groups: JoinGroup[] }).groups,
    // The "Still in the party?" questions (#4895): DM only (the door answers anyone else with nothing).
    leaveQuestions: async (ref) => ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/leave-questions`)) as { groups: LeaveGroup[] }).groups,
    // Queued for the bot (202 {answer}), then waited for: a "left" answer gives its batch, for Undo through `answerJoin`'s undo.
    answerLeave: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/leave-questions/answer`, { character: change.character, answer: change.answer })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // The heads-ups from this session (#3695): DM only; the door answers anyone else with not-allowed.
    continuityHits: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/continuity`)) as ContinuityHits,
    // Queued for the bot and waited for: its count says how many heads-ups it found.
    askContinuity: async (ref) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/continuity/ask`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = (await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`)) as { count?: unknown };
      return { count: typeof got.count === "number" ? got.count : 0 };
    },
    // Queued for the bot and waited for. A "refused" is the bot's own word for a clash it cannot settle yet (#3695).
    answerContinuity: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/continuity/answer`, change)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`, undefined, undefined, { refused: "refused" });
    },
    // The deeds the party did, waiting to be recorded (#3591): DM only; the door answers anyone else with nothing.
    deedQuestions: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/deeds`)) as DeedQuestions,
    // Queued for the bot (202 {answer}), then waited for: a record gives its batch, for Undo; an undo gives null.
    answerDeed: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const body = "undo" in change ? { choice: "undo", batch: change.undo } : { choice: change.answer, deed: change.deed };
      const queued = (await call("POST", `${base}/deeds/answer`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      // A deed answer can fail with "refused" (the bot does not know a name in it): the card shows its own words for that (#3591).
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`, undefined, undefined, { refused: "refused" });
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // What the characters learned (#3863 step 2): the bot is asked first and waited for, then the list is read. DM only.
    revealQuestions: async (ref) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/reveals/ask`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return ((await call("GET", `${base}/reveals`)) as { proposals: RevealProposal[] }).proposals;
    },
    // Keep or Not this, queued for the bot and waited for; a keep gives its batch for Undo (#3863 step 2).
    answerReveal: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/reveals/answer`, { id: change.id, answer: change.answer })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // Queued for the bot (202 {answer}), then waited for: the batch and `given` of an answer; the batch of an Undo is in its path.
    answerJoin: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued =
        change.action === "undo"
          ? ((await call("POST", `${base}/join-questions/${encodeURIComponent(String(change.batch))}/undo`)) as { answer: string })
          : ((await call("POST", `${base}/join-questions/answer`, { group: change.group, answer: change.action })) as { answer: string });
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return {
        batch: typeof got.batch === "number" ? got.batch : null,
        given: typeof got.given === "number" ? got.given : null,
      };
    },
    // A player character's stretches in the party (#4778 a2): DM only; the door answers anyone else with nothing.
    partyRecord: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/party-record`)) as { characters: PartyRecordCharacter[] }).characters,
    // A leave, a return or an Undo is queued for the bot (202 {answer}) and waited for: a leave gives its batch, a return its batch and what it asks.
    partyLeave: async (ref, character, why) => {
      const got = await partyAnswer(ref, await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/party-record/leave`, { character, why }));
      return { batch: got.batch ?? null };
    },
    partyBack: async (ref, character) => {
      const got = await partyAnswer(ref, await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/party-record/back`, { character }));
      return { batch: got.batch ?? null, asked: got.asked ?? null };
    },
    // "Catch up" (#4793): who is behind on an entry, and catching one member up (queued, then waited for: `told` and `batch`).
    catchUpChoices: async (ref, entry) => {
      const got = await partyAnswer(ref, await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/catch-up/choices`, { entry }));
      return { choices: got.choices ?? [], more: got.more ?? 0 };
    },
    catchUp: async (ref, member, entry) => {
      const got = await partyAnswer(ref, await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/catch-up`, { member, entry }));
      return { batch: got.batch ?? null, told: got.told ?? null };
    },
    // A pick of a newer version of a followed set (#4855): queued for the bot; waits for its answer, which has the counts and the id
    // for Undo. A refusal comes back as the bot's word (`changed`, `gone`, `no_room`, `too_many`, ...).
    ruleUpdatePick: async (ref, version, kept) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/rule-update/pick`, { version, kept })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { kept: got.kept ?? kept.length, left: got.left ?? 0, take: typeof got.take === "number" ? got.take : null };
    },
    // Undo of the update DMbot took (#4867): queued for the bot; waits for its answer, which has the version it went back to.
    ruleUpdateUndoLast: async (ref, version) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/rule-update/undo-last`, { version })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { back: typeof got.back === "number" ? got.back : null };
    },
    // Pick of a pack's newer version (#4873): queued for the bot; waits for its answer, which has the counts and the id for Undo.
    packUpdatePick: async (ref, setId, version, kept) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/pack-updates/pick`, { setId, version, kept })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      // The bot says how many were left out; without it the answer can't be shown, so it is a refusal (#4873).
      if (typeof got.left !== "number") throw new ApiError("server");
      return {
        kept: got.kept ?? kept.length,
        left: got.left,
        skipped: typeof got.skipped === "number" ? got.skipped : 0,
        take: typeof got.take === "number" ? got.take : null,
      };
    },
    // Undo of a pack pick (#4873): queued for the bot, and this waits for its answer (`too_late` past the day).
    packUpdateUndo: async (ref, take) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/pack-updates/${encodeURIComponent(String(take))}/undo`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // Undo of the pack update DMbot took (#4872): queued for the bot; waits for its answer, which has the version it went back to.
    packUpdateUndoLast: async (ref, setId, version) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/pack-updates/undo-last`, { setId, version })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { back: typeof got.back === "number" ? got.back : null };
    },
    // Undo of a pick (#4855): queued for the bot, and this waits for its answer (`too_late` past the day).
    ruleUpdateUndo: async (ref, take) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/rule-update/${encodeURIComponent(String(take))}/undo`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    partyUndo: async (ref, batch) => {
      await partyAnswer(ref, await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/party-record/${encodeURIComponent(String(batch))}/undo`));
    },
    // The party's player characters for the "Some know" picker (#4680 part 1): DM only; the door answers anyone else with nothing.
    partyCharacters: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/party-characters`)) as { characters: PartyCharacter[] }).characters,
    // Queued for the bot (202 {answer}); this waits for it and gives back the batch of a make, for Undo (#4598).
    answerSecret: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const body = change.action === "undo" ? { action: "undo", batch: String(change.batch) } : change;
      const queued = (await call("POST", `${base}/secret-questions/change`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // A story thread's clues (#4625): DM only; the door answers anyone else with nothing.
    threadClues: async (ref, thread) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/threads/${encodeURIComponent(thread)}/clues`)) as ThreadClues,
    // The table's guesses about a thread (#4665): DM only; the door answers anyone else with nothing.
    threadGuesses: async (ref, thread) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/threads/${encodeURIComponent(thread)}/guesses`)) as ThreadGuesses,
    // Queued for the bot (202 {answer}); this waits for it and gives back the batch of a change, for Undo (#4625).
    changeThreadClues: async (ref, thread, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/threads/${encodeURIComponent(thread)}`;
      const body = change.action === "undo" ? { action: "undo", batch: String(change.batch) } : change;
      const queued = (await call("POST", `${base}/clues/change`, body)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`/me/campaigns/${encodeURIComponent(ref)}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    threadCard: async (ref, thread) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/threads/${encodeURIComponent(thread)}/card`)) as ThreadCard,
    // A road's or route place's condition (#4451): queued for the bot, waited for, and no batch of its own (the Undo is undoRouteLength).
    roadCondition: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/road-condition`, change)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    keepRoadCondition: async (ref, id) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/road-condition/keep`, { id })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    showRoadCondition: async (ref, id, shown) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/road-condition/show`, { id, shown })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // Takes back a route-length change by its batch (the same undo route as the length page): a road's condition changes come back with it (#4451).
    undoRouteLength: async (ref, batch) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/route-length/${batch}/undo`, {})) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // A route's stated length (#4392): queued for the bot and waited for. A set or a keep gives its batch; null means nothing changed.
    changeRouteLength: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await (change.action === "set"
        ? call("POST", `${base}/route-length`, { on: change.on, id: change.id, words: change.words })
        : change.action === "keep"
          ? call("POST", `${base}/route-length/keep`, { on: change.on, id: change.id })
          : call("POST", `${base}/route-length/${change.batch}/undo`, {}))) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // A character's lineage and class (#3925): DM only; the door answers anyone else with nothing.
    nodeTraits: async (ref, node) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/web/nodes/${encodeURIComponent(node)}/traits`)) as { traits: NodeTrait[] }).traits,
    // The story threads DMbot found (#4482): read only, DM only (the door answers anyone else with nothing).
    storyThreads: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/threads`)) as StoryThreads,
    // The open plot threads (#3696, slice 1): read only, DM only (the door answers anyone else with nothing).
    plotThreads: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/plot-threads`)) as PlotThreads,
    // Adds one name to a thread with its part (#3696, slice 2b): queued for the bot (202 {answer}) and waited for; no batch, no Undo.
    addPlotThreadInvolved: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/involve`, { thread: change.thread, who: change.who, role: change.role })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // A new thread or promise (#3696, slice 2c): queued for the bot and waited for; a done answer carries the new thread's id.
    addPlotThread: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/new`, { kind: change.kind, state: change.state, name: change.name })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      const got = (await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`)) as { id?: unknown };
      if (typeof got.id !== "string" || got.id === "") throw new ApiError("server");
      return { id: got.id };
    },
    // A promise's due note (empty clears it), and who made it or it was made to (#3696, slice 2c): queued, waited for, no Undo.
    setPlotThreadDue: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/due`, { promise: change.promise, due: change.due })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    addPlotThreadParty: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/party`, { promise: change.promise, side: change.side, who: change.who })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // A link of an event to a thread, and a thread that led to another (#3696, slice 2d): queued for the bot and waited for; no Undo.
    linkPlotThreadEvent: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/link`, change)) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    plotThreadLedTo: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/led-to`, { from: change.from, to: change.to })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // A state change (or "check") is queued for the bot (202 {answer}) and waited for; the answer has no batch, so no Undo (#3696, slice 2a).
    changePlotThreadState: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued = (await call("POST", `${base}/plot-threads/state`, { thread: change.thread, state: change.state })) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
    },
    // Queued for the bot (202 {answer}); this waits for it and gives back the batch of a keep, for Undo (#4482).
    changeStoryThreads: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      let queued: { answer: string } | undefined;
      if (change.action === "keep") {
        const body: Record<string, unknown> = { id: change.id, on: change.on, lines: change.lines };
        if (change.name !== undefined) body.name = change.name;
        queued = (await call("POST", `${base}/threads/keep`, body)) as { answer: string };
      } else if (change.action === "not") {
        queued = (await call("POST", `${base}/threads/not`, { id: change.id })) as { answer: string };
      } else if ("keep" in change) {
        queued = (await call("POST", `${base}/threads/undo`, { keep: String(change.keep) })) as { answer: string };
      } else {
        queued = (await call("POST", `${base}/threads/undo`, { not: change.not })) as { answer: string };
      }
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null };
    },
    // Queued for the bot like the connection edits (202 {answer}); this waits for it and gives back the change it made, for Undo.
    changeWorldNotes: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued =
        change.action === "keep"
          ? ((await call("POST", `${base}/world-notes/keep`, { ids: change.ids })) as { answer: string })
          : change.action === "not"
            ? ((await call("POST", `${base}/world-notes/not`, { id: change.id })) as { answer: string })
            : change.action === "bias"
              ? ((await call("POST", `${base}/world-notes/bias`, { id: change.id, step: change.step })) as { answer: string })
              : ((await call("POST", `${base}/world-notes/${change.batch}/undo`, {})) as { answer: string });
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { kept: typeof got.kept === "number" ? got.kept : null, batch: typeof got.batch === "number" ? got.batch : null };
    },
    // The Maps page (#4347): the list is read here; a press is queued for the bot, waited for, and gives back the change for Undo.
    maps: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/maps`)) as MapsRead,
    // A map's pins, scale, grid and north (#4672): DM only; the door answers anyone else with nothing.
    mapFeatures: async (ref, map) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/maps/${encodeURIComponent(map)}/features`)) as MapFeatures,
    changeMaps: async (ref, change) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const queued =
        change.action === "use"
          ? ((await call("POST", `${base}/maps/${encodeURIComponent(change.id)}/use`, { yes: change.yes })) as { answer: string })
          : change.action === "show"
            ? ((await call("POST", `${base}/maps/${encodeURIComponent(change.id)}/show`, { shown: change.shown })) as { answer: string })
            : change.action === "remove"
              ? ((await call("POST", `${base}/maps/${encodeURIComponent(change.id)}/remove`, {})) as { answer: string })
              : change.action === "takeback"
                ? ((await call("POST", `${base}/maps/${encodeURIComponent(change.id)}/takeback`, {})) as { answer: string })
                : ((await call("POST", `${base}/maps/${encodeURIComponent(String(change.batch))}/undo`, {})) as { answer: string });
      if (!queued?.answer) throw new ApiError("server");
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`);
      return { batch: typeof got.batch === "number" ? got.batch : null } as MapsResult;
    },
    // A picture sent to be a map (#4347): the file itself goes as the body, with the name and the person's own yes in headers, and
    // `onLook` hears how far it has got (sending the file, then DMbot saving it) so the page can show the wait. Then the bot's answer is
    // waited for, as for every press, and gives back the map and the change for Undo.
    addMap: async (ref, upload, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}`;
      const started = Date.now();
      let moved = started;
      const look = (stage: "sending" | "saving"): void =>
        onLook?.({ stage, seconds: Math.floor((Date.now() - started) / 1000), quiet: Math.floor((Date.now() - moved) / 1000) });
      const queued = await new Promise<{ answer?: string }>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${root}${base}/maps`);
        xhr.withCredentials = true;
        xhr.setRequestHeader("X-DMbot-Request", "1");
        xhr.setRequestHeader("Content-Type", upload.file.type || "application/octet-stream");
        xhr.setRequestHeader("X-Map-Title", encodeURIComponent(upload.title));
        xhr.setRequestHeader("X-Map-Rights", upload.rights ? "yes" : "no");
        xhr.upload.onprogress = () => {
          moved = Date.now();
          look("sending");
        };
        xhr.onerror = () => reject(new ApiError("network"));
        xhr.ontimeout = () => reject(new ApiError("network"));
        xhr.onload = () => {
          if (xhr.status === 401) {
            if (typeof window !== "undefined") window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
            return reject(new ApiError("signed-out"));
          }
          let body: { answer?: string; error?: unknown; detail?: unknown } = {};
          try {
            body = JSON.parse(xhr.responseText) as typeof body;
          } catch {
            // (no words in the answer: the status says enough)
          }
          if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
          const code = String(body.error ?? (typeof body.detail === "string" ? body.detail : ""));
          reject(new ApiError(xhr.status === 413 ? "bad-file" : (errorKinds[code] ?? "server")));
        };
        look("sending");
        xhr.send(upload.file);
      });
      if (!queued?.answer) throw new ApiError("server");
      moved = Date.now();
      let last = "";
      const got = await waitForAnswer(`${base}/web/answers/${encodeURIComponent(queued.answer)}`, (answer) => {
        if (answer.state !== last) moved = Date.now(); // (a new stage counts as moving)
        last = answer.state;
        look("saving");
      });
      const made = got as { mapId?: string | null; batch?: number | null };
      return { mapId: typeof made.mapId === "string" ? made.mapId : "", batch: typeof made.batch === "number" ? made.batch : null };
    },
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

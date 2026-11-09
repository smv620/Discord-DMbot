// The "campaign" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `CAMPAIGN_METHODS` there, and a test keeps the two in step.
import type {
  PictureAction,
  PictureActionResult,
  PictureList,
  SessionNotes,
  SessionNotesList,
  RecapStyle,
  RecapStyleAction,
  CampaignSpeaker,
  SpeakerRemoval,
  HouseFileState,
  AiFeedRead,
  LoreUpload,
  CampaignPage,
  CampaignSettingAnswer,
  CharacterSheets,
  FixAnswer,
  FixMode,
  GameMasterCandidate,
  GameMasters,
  HomebrewCampaign,
  HomebrewPage,
  HouseRuleCampaign,
  HouseRules,
  OptionalRules,
  RuleDecision,
  RuleSharing,
  RuleSharingAnswer,
  SharedRulesCampaign,
  SheetAnswer,
  StretchLine,
  Timeline,
  ScriptOffer,
  TranscriptCampaign,
  TranscriptSessionItem,
  TranscriptView,
} from "./api";
import {
  ApiError,
  errorKinds,
  fixErrorKinds,
  sharedOf,
} from "./api";
import type { AccountApi, ApiErrorKind, ApiHelpers } from "./api";

/** The picture action's own 409 details (#3275). Only that action reads them, so they stay out of the shared table. */
const pictureErrorKinds: Record<string, ApiErrorKind> = {
  ...errorKinds,
  "permanent-full": "permanent-full",
  refused: "refused",
};

/** The script download's own refusal (#3244): a session with no script. Only that call reads it. */
const scriptErrorKinds: Record<string, ApiErrorKind> = { ...errorKinds, no_script: "no-script" };

/** The make-script action's own refusals (#3899): the plan gate, scripts off, and one already made. */
const makeScriptErrorKinds: Record<string, ApiErrorKind> = { ...errorKinds, plan: "plan-needed", off: "script-off", already: "script-already" };

/** The route's own action names; the page's names for the same things are mapped here only (#3275). */
const PICTURE_WIRE_ACTION: Record<PictureAction, string> = {
  "make-private": "private",
  "make-public": "public",
  permanent: "permanent",
  "not-permanent": "unpermanent",
  delete: "delete",
};

/** What the must-haves routes refuse with (#3329, CloudDev-lead's #3340): `too_many` would otherwise read as the game masters' limit. */
const MUSTS_REFUSALS: Record<string, ApiErrorKind> = { too_many: "musts-full", empty: "musts-empty", too_long: "musts-too-long", bad_arg: "bad-choice" };

export default function campaignArea({ call }: ApiHelpers): Partial<AccountApi> {
  return {
    transcriptCampaigns: async () =>
      (
        (await call("GET", "/me/transcripts")) as {
          campaigns: TranscriptCampaign[];
        }
      ).campaigns,
    transcriptSessions: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts`,
      )) as {
        name: string;
        keepDays?: number | null;
        sessions: TranscriptSessionItem[];
      },
    sessionNotesList: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/session-notes`)) as SessionNotesList,
    pictures: async (campaignRef, filter) => {
      const params = new URLSearchParams();
      if (filter.session !== undefined) params.set("session", String(filter.session));
      if (filter.poster) params.set("poster", filter.poster);
      if (filter.q) params.set("q", filter.q);
      if (filter.before) params.set("before", filter.before);
      const query = params.toString();
      return (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/pictures${query ? `?${query}` : ""}`,
      )) as PictureList;
    },
    pictureAction: async (campaignRef, code, action) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/pictures/${encodeURIComponent(code)}`,
        { action: PICTURE_WIRE_ACTION[action] },
        false,
        undefined,
        false,
        pictureErrorKinds,
      )) as PictureActionResult,
    sessionNotes: async (campaignRef, at) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/session-notes/${at}`)) as SessionNotes,
    transcript: async (campaignRef, number, version, offset = 0) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}?version=${version}&offset=${offset}`,
      )) as TranscriptView,
    transcriptFile: async (campaignRef, number, version) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/download?version=${version}`,
        undefined,
        true,
      )) as Blob,
    transcriptScript: async (campaignRef, number) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/script`,
        undefined,
        true,
        undefined,
        false,
        scriptErrorKinds,
      )) as Blob,
    scriptOffer: async (campaignRef, number) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/script-offer`)) as ScriptOffer,
    // One press queues the job for the bot, then it is polled like any queued change (a script can take minutes).
    makeScript: async (campaignRef, number, onLook) => {
      const base = `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/script`;
      const queued = (await call("POST", base, {}, false, undefined, false, makeScriptErrorKinds)) as { id: string };
      if (!queued?.id) throw new ApiError("server");
      let misses = 0;
      for (let i = 0; i < 300; i++) {
        let got = null as { state?: string; error?: string; progress?: unknown } | null;
        try {
          got = (await call("GET", `${base}/${encodeURIComponent(queued.id)}`, undefined, false, undefined, false, makeScriptErrorKinds)) as NonNullable<typeof got>;
          misses = 0;
        } catch (error) {
          // A refusal ends it; a trouble in reaching the server is waited out, a few looks, then said.
          if (error instanceof ApiError && ["signed-out", "not-found", "not-allowed", "plan-needed", "script-off", "script-already"].includes(error.kind)) throw error;
          if ((misses += 1) >= 5) throw new ApiError("server");
        }
        if (got) onLook?.(sharedOf(got.progress as never));
        if (got?.state === "done") return;
        if (got?.state === "failed") throw new ApiError(makeScriptErrorKinds[got.error ?? ""] ?? "server");
        await new Promise((r) => setTimeout(r, 2000));
      }
      throw new ApiError("still-waiting");
    },
    transcriptStretch: async (campaignRef, number, skip) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/stretch?speaker=${skip.speaker}&fromMs=${skip.fromMs}&toMs=${skip.toMs}`,
        )) as { lines: StretchLine[] }
      ).lines,
    transcriptPutBack: async (campaignRef, number, skip) =>
      (
        (await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/put-back`, {
          speaker: skip.speaker,
          fromMs: skip.fromMs,
          toMs: skip.toMs,
        })) as { changed: number }
      ).changed,
    transcriptLeaveOut: async (campaignRef, number, skip) =>
      (
        (await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/leave-out`, {
          speaker: skip.speaker,
          fromMs: skip.fromMs,
          toMs: skip.toMs,
        })) as { changed: number }
      ).changed,
    setCampaignSetting: async (campaignRef, choice) =>
      (
        (await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/settings`, {
          setting: choice.setting,
          value: choice.value,
          ...(choice.confirm ? { confirm: true } : {}),
          ...(choice.on === undefined ? {} : { on: choice.on }),
        })) as { answer: string }
      ).answer,
    campaignOptionalRules: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/optional-rules`)) as OptionalRules,
    campaignGameMasters: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/game-masters`)) as GameMasters,
    campaignSheets: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/sheets`)) as CharacterSheets,
    chooseSheet: async (campaignRef, entity, choice) => {
      const queued = (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/sheets/${encodeURIComponent(entity)}`,
        choice,
      )) as { answer: string };
      if (!queued?.answer) throw new ApiError("server");
      return queued.answer;
    },
    sheetAnswer: async (campaignRef, answer) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/sheets/answers/${encodeURIComponent(answer)}`,
      )) as SheetAnswer,
    fixTranscriptLine: async (campaignRef, number, line, text) => {
      const queued = (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/fix`,
        { speaker: line.speaker, startedMs: line.startedMs, text },
        false,
        undefined,
        false,
        fixErrorKinds,
      )) as { answer: string; mode: FixMode };
      if (!queued?.answer) throw new ApiError("server");
      return queued;
    },
    fixAnswer: async (campaignRef, number, answer) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/fix/${encodeURIComponent(answer)}`,
        undefined,
        false,
        undefined,
        false,
        fixErrorKinds,
      )) as FixAnswer,
    campaignGameMasterCandidates: async (campaignRef, query) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/game-masters/candidates?q=${encodeURIComponent(query)}`,
        )) as { people: GameMasterCandidate[] }
      ).people,
    campaignSettingAnswer: async (campaignRef, answer) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/settings/answers/${encodeURIComponent(answer)}`,
      )) as CampaignSettingAnswer,
    timeline: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/timeline`,
      )) as Timeline,
    sharedRules: async () =>
      ((await call("GET", "/me/shared-rules")) as { campaigns: SharedRulesCampaign[] })
        .campaigns,
    houseRuleCampaigns: async () =>
      (
        (await call("GET", "/me/house-rules")) as {
          campaigns: HouseRuleCampaign[];
        }
      ).campaigns,
    houseRules: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/house-rules`,
      )) as HouseRules,
    ruleDecisions: async (campaignRef) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/rule-decisions`)) as { decisions: RuleDecision[] }).decisions,
    ruleSharing: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/rule-sharing`,
      )) as RuleSharing,
    chooseRuleSharing: async (campaignRef, choice) =>
      (
        (await call(
          "POST",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/rule-sharing`,
          choice,
        )) as { answer: string }
      ).answer,
    ruleSharingAnswer: async (campaignRef, answer) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/rule-sharing/answers/${encodeURIComponent(answer)}`,
      )) as RuleSharingAnswer,
    houseRulesFile: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/house-rules/download`,
        undefined,
        true,
      )) as Blob,
    improvement: async () =>
      ((await call("GET", "/me/improvement")) as { improve: boolean }).improve,
    setImprovement: async (on) =>
      (
        (await call("PUT", "/me/improvement", { improve: on })) as {
          improve: boolean;
        }
      ).improve,
    homebrewCampaigns: async () =>
      ((await call("GET", "/me/homebrew")) as { campaigns: HomebrewCampaign[] })
        .campaigns,
    homebrew: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/homebrew`,
      )) as HomebrewPage,
    homebrewFile: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/homebrew/download`,
        undefined,
        true,
      )) as Blob,
    campaignPage: async (ref) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}`)) as CampaignPage,
    recapStyle: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/recap-style`)) as RecapStyle,
    changeRecapStyle: async (ref, action, arg = "") =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/recap-style`, { action, arg })) as { id: string },
    campaignSpeakers: async (ref) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/speakers`)) as { speakers: CampaignSpeaker[] }).speakers,
    removeSpeaker: async (ref, speaker) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/speakers/${encodeURIComponent(speaker)}/remove`)) as { id: string },
    speakerRemoval: async (ref, id) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/speakers/removals/${encodeURIComponent(id)}`)) as SpeakerRemoval,
    saveRecapMusts: async (ref, lines) =>
      (await call("PUT", `/me/campaigns/${encodeURIComponent(ref)}/recap-style/must-haves`, { lines }, false, undefined, false, MUSTS_REFUSALS)) as { id: string },
    recapMustsDefaults: async (ref) =>
      (await call("DELETE", `/me/campaigns/${encodeURIComponent(ref)}/recap-style/must-haves`, undefined, false, undefined, false, MUSTS_REFUSALS)) as { id: string },
    recapStyleAction: async (ref, id) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/recap-style/${encodeURIComponent(id)}`)) as RecapStyleAction,
    loreAdd: async (ref, input) =>
      ((await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/lore`, input)) as { upload: LoreUpload }).upload,
    houseFile: async (ref, id) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/house-file${id ? `/${encodeURIComponent(id)}` : ""}`)) as HouseFileState,
    houseFileAdd: async (ref, input) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/house-file`, input)) as HouseFileState,
    aiFeed: async (ref, kind, job, after) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/ai-feed/${kind}/${encodeURIComponent(job)}?after=${after}`)) as AiFeedRead,
    loreRead: async (ref, id) =>
      ((await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/lore${id ? `/${encodeURIComponent(id)}` : ""}`)) as { upload: LoreUpload | null }).upload,
  };
}

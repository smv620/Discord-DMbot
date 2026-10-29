// The "campaign" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `CAMPAIGN_METHODS` there, and a test keeps the two in step.
import type {
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
  RuleSharing,
  RuleSharingAnswer,
  SharedRulesCampaign,
  SheetAnswer,
  StretchLine,
  Timeline,
  TranscriptCampaign,
  TranscriptSessionItem,
  TranscriptView,
} from "./api";
import {
  ApiError,
  fixErrorKinds,
} from "./api";
import type { AccountApi, ApiHelpers } from "./api";

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
  };
}

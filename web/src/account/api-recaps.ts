// The "recaps" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `RECAPS_METHODS` there, and a test keeps the two in step.
import type {
  FixResult,
  ImportCheck,
  ImportMember,
  ImportPreview,
  ImportProgress,
  ImportStarted,
  PastSessions,
  PlotCheck,
  PlotIssue,
  PlotMessage,
  PlotProposal,
  PublicRecap,
  Recap,
  RecapAudioLinks,
  RecapBeat,
  RecapChoices,
  RecapMonth,
  RecapRemake,
  RecapShare,
  Rewritten,
  SessionReport,
  VoiceResult,
} from "./api";
import {
  busyAsBusy,
  recapFrom,
  withLinks,
} from "./api";
import type { AccountApi, ApiHelpers } from "./api";

export default function recapsArea({ call, url }: ApiHelpers): Partial<AccountApi> {
  return {
    recapAudioUrl: () => url("/billing/recap-audio"),
    recaps: async () =>
      ((await call("GET", "/me/recaps")) as { recaps: Recap[] }).recaps,
    pastSessions: async (campaignRef) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/past-sessions`)) as PastSessions,
    sessionReport: async (campaignRef, at) =>
      (await call("GET", `/me/campaigns/${encodeURIComponent(campaignRef)}/sessions/${at}/report`)) as SessionReport,
    makeSessionReport: async (campaignRef, at) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/sessions/${at}/report/make`, {})) as SessionReport,
    // Written at once by the API (an answer is the DM's bookkeeping, not a change to the story: Supervisor on
    // #2661), so there is nothing to wait for or poll; the page reads the report again.
    answerReport: async (campaignRef, at, id, action) => {
      await call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/sessions/${at}/report`, { id, action });
    },
    setBeatPresent: async (recapRef, n, present) =>
      (await call("PUT", `/me/recaps/${encodeURIComponent(recapRef)}/beats/${n}/present`, { present })) as RecapBeat,
    makeRecap: async (recapRef, replace) =>
      recapFrom(await busyAsBusy(() => call("POST", `/me/recaps/${encodeURIComponent(recapRef)}/again`, { replace }))),
    addRecap: async (campaignRef, input) =>
      recapFrom(
        await busyAsBusy(() => call("POST", `/me/campaigns/${encodeURIComponent(campaignRef)}/recaps/upload`, input)),
      ),
    recapList: async () => {
      const got = (await call("GET", "/me/recaps")) as {
        recaps: Recap[];
        recapsThisMonth?: RecapMonth[];
      };
      return { recaps: got.recaps, thisMonth: got.recapsThisMonth ?? [] };
    },
    recap: async (ref) =>
      recapFrom(await call("GET", `/me/recaps/${encodeURIComponent(ref)}`)),
    saveRecap: async (ref, summary, housekeeping) =>
      recapFrom(
        await call("PUT", `/me/recaps/${encodeURIComponent(ref)}`, {
          summary,
          housekeeping,
        }),
      ),
    approveRecap: async (ref, choice, options) =>
      recapFrom(
        await call("POST", `/me/recaps/${encodeURIComponent(ref)}/approve`, {
          choice,
          ...(options?.post ? { post: true } : {}),
        }),
      ),
    approveUnchecked: async (ref, textHash) =>
      recapFrom(await call("POST", `/me/recaps/${encodeURIComponent(ref)}/approve-unchecked`, { textHash })),
    recapAudioLinks: async (ref) =>
      (
        (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/audio`)) as {
          links: RecapAudioLinks;
        }
      ).links,
    recapChoices: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/recap-choices`,
      )) as RecapChoices,
    setRecapChoice: async (campaignRef, which, on) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/recap-choices`,
        {
          which,
          on,
        },
      )) as RecapChoices,
    askRecapAudioAgain: async (ref) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/audio/again`,
      )) as Recap,
    plotCheck: async (ref) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/plot`)) as PlotCheck,
    answerPlotIssue: async (ref, issue, answer) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/plot/${encodeURIComponent(issue)}/answer`,
        answer,
      )) as PlotIssue,
    plotChatSend: async (ref, issue, text) => {
      await call("POST", `/me/recaps/${encodeURIComponent(ref)}/plot/${encodeURIComponent(issue)}/chat`, { text });
    },
    plotChat: async (ref, issue) =>
      ((await call("GET", `/me/recaps/${encodeURIComponent(ref)}/plot/${encodeURIComponent(issue)}/chat`)) as {
        messages: PlotMessage[];
      }).messages,
    decidePlotProposal: async (ref, issue, proposal, action) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/plot/${encodeURIComponent(issue)}/proposals/${encodeURIComponent(proposal)}`,
        { action },
      )) as PlotProposal,
    recapAudioRemake: async (ref) =>
      (await call(
        "GET",
        `/me/recaps/${encodeURIComponent(ref)}/audio/remake`,
      )) as RecapRemake,
    sendFix: async (ref, text) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/instructions`,
        { text },
      )) as { requestId: string },
    fixResult: async (ref, requestId) =>
      (await call(
        "GET",
        `/me/recaps/${encodeURIComponent(ref)}/instructions/${encodeURIComponent(requestId)}`,
      )) as FixResult,
    sendVoice: async (ref, clip) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/voice`,
        undefined,
        false,
        clip,
      )) as { requestId: string },
    voiceResult: async (ref, requestId) =>
      (await call(
        "GET",
        `/me/recaps/${encodeURIComponent(ref)}/voice/${encodeURIComponent(requestId)}`,
      )) as VoiceResult,
    rewriteWith: async (ref, requestId, corrections, style) => {
      const got = (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/rewrite`,
        { requestId, corrections, style },
      )) as Rewritten;
      return { ...got, recap: recapFrom(got.recap) };
    },
    // Google Doc links ride along with the files (#2140); the API opens each and names it "Google Doc N.txt".
    importPreview: async (ref, files, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/preview`,
        withLinks({ files }, links),
      )) as ImportPreview,
    importMembers: async (ref, q) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(ref)}/imports/members?q=${encodeURIComponent(q)}`,
        )) as { members: ImportMember[] }
      ).members,
    importCheck: async (ref, files, mapping, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/check`,
        withLinks({ files, mapping }, links),
      )) as ImportCheck,
    importStart: async (ref, files, plan, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports`,
        withLinks({ files, plan }, links),
      )) as ImportStarted,
    importProgress: async (ref, importId) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/${encodeURIComponent(importId)}`,
      )) as ImportProgress,
    shareRecap: async (ref, action) =>
      (await call("POST", `/me/recaps/${encodeURIComponent(ref)}/share`, {
        action,
      })) as RecapShare,
    publicRecap: async (token) =>
      (await call(
        "GET",
        `/recaps/${encodeURIComponent(token)}`,
        undefined,
        false,
        undefined,
        true,
      )) as PublicRecap,
    decideRecap: async (ref, action) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/${action}`,
      )) as Recap,
  };
}

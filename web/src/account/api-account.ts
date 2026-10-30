// The "account" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `ACCOUNT_METHODS` there, and a test keeps the two in step.
import type {
  BackupFile,
  Character,
  HandoverLook,
  JobProgress,
  MyEmail,
  MyLines,
  MyLinesStatus,
  RestoreStatus,
  SupportMessage,
  SupportQuestion,
} from "./api";
import {
  ApiError,
  BACKUP_FILE_TIMEOUT_MS,
  BACKUP_GIVE_UP_MS,
  BACKUP_POLL_MAX_MS,
  BACKUP_POLL_MS,
  backupReasons,
  safeBackupName,
} from "./api";
import type { AccountApi, ApiHelpers } from "./api";

export default function accountArea({ call, restoreCall, url }: ApiHelpers): Partial<AccountApi> {
  return {
    emailSignInRequest: async (email) => {
      await call("POST", "/auth/email/request", { email });
    },
    emailSignInUse: async (token) => {
      await call("POST", "/auth/email/verify", { token });
    },
    offerHandover: async (ref, email) => {
      await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/handover`, { email });
    },
    deleteCampaign: async (ref, name) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/delete`, { name })) as { channelsLeft: boolean },
    restoreStage: async (serverId, file, replaceRef) => {
      const query = replaceRef === undefined ? "" : `?replace=${encodeURIComponent(replaceRef)}`;
      // The file goes as it is, not as JSON: it can be 25 MB, and the API reads it with a running cap.
      const raw = new Blob([file], { type: "application/octet-stream" });
      return (await restoreCall("POST", `/me/restores/${encodeURIComponent(serverId)}${query}`, undefined, false, raw)) as {
        id: string;
        name: string;
        partsWanted: number;
      };
    },
    restorePart: async (serverId, id, number, file) => {
      const raw = new Blob([file], { type: "application/octet-stream" });
      await restoreCall(
        "PUT",
        `/me/restores/${encodeURIComponent(serverId)}/${encodeURIComponent(id)}/parts/${number}`,
        undefined,
        false,
        raw,
      );
    },
    restoreGo: async (serverId, id, without) => {
      await restoreCall("POST", `/me/restores/${encodeURIComponent(serverId)}/${encodeURIComponent(id)}/go`, { without });
    },
    restoreStatus: async (serverId, id) => {
      try {
        const tail = id === undefined ? "" : `/${encodeURIComponent(id)}`;
        return (await restoreCall("GET", `/me/restores/${encodeURIComponent(serverId)}${tail}`)) as RestoreStatus;
      } catch (error) {
        if (error instanceof ApiError && error.kind === "not-found") return null;
        throw error;
      }
    },
    restoreCancel: async (serverId, id) => {
      await restoreCall("DELETE", `/me/restores/${encodeURIComponent(serverId)}/${encodeURIComponent(id)}`);
    },
    campaignBackup: async (ref, onProgress, onFile) => {
      const base = `/me/campaigns/${encodeURIComponent(ref)}/backup`;
      const asked = (await call("POST", base)) as { id: string };
      const copy = `${base}/${encodeURIComponent(asked.id)}`;
      const began = Date.now();
      let wait = BACKUP_POLL_MS;
      type Status = JobProgress & {
        ready: boolean;
        fileName?: string | null;
        fileWaiting?: boolean;
        parts?: { n: number }[];
        partsTotal?: number;
      };
      let got: Status;
      for (;;) {
        got = (await call("GET", copy)) as Status;
        onProgress?.(got);
        if (got.state === "failed") throw new ApiError(backupReasons[got.reason ?? ""] ?? "backup-failed");
        if (got.ready) break;
        // DMbot keeps a copy 15 minutes; a copy not ready by then is not coming.
        if (Date.now() - began > BACKUP_GIVE_UP_MS) throw new ApiError("backup-failed");
        await new Promise((resolve) => setTimeout(resolve, wait));
        wait = Math.min(wait * 1.5, BACKUP_POLL_MAX_MS);
      }
      // The main file, then each transcripts file still waiting, each fetched once (DMbot deletes a file as it
      // sends it). The names are Discord's own, made from the main file's name. One that fails fails the set:
      // a backup with a file missing would restore without part of the story.
      const main = safeBackupName(got.fileName ?? "", "campaign.dmbot.json.gz");
      const stem = main.replace(/\.dmbot\.json\.gz$/, "") || "campaign";
      // An API without the transcripts files says nothing of them (no `fileWaiting`, no `partsTotal`): a main file
      // alone would look complete, so it is refused.
      if (typeof got.partsTotal !== "number" || typeof got.fileWaiting !== "boolean") throw new ApiError("backup-failed");
      const parts = (got.parts ?? []).map((p) => p.n);
      // A broken set is never resumed: files are handed over once, so a copy whose main file was already taken, or
      // that lists fewer files than it had, can never be a whole backup. Refused before anything is fetched.
      if (!got.fileWaiting || parts.length !== got.partsTotal) throw new ApiError("backup-unfinished");
      const total = 1 + parts.length;
      const files: BackupFile[] = [];
      onFile?.(0, total);
      const take = async (path: string, name: string, first: boolean): Promise<void> => {
        try {
          // A file that does not arrive in time is a stuck download, said as one (the page keeps its clock running).
          const arrives = call("GET", `${copy}${path}`, undefined, true) as Promise<Blob>;
          const late = new Promise<never>((_, reject) => setTimeout(() => reject(new ApiError(first ? "backup-failed" : "backup-part")), BACKUP_FILE_TIMEOUT_MS));
          arrives.catch(() => undefined); // (a file that arrives after the wait is over is dropped, not an unhandled error)
          files.push({ name, blob: await Promise.race([arrives, late]) });
        } catch (error) {
          // The main file keeps its own refusals (not yours, gone); a later file that fails is a broken set,
          // unless the trouble is the connection or the sign-in, which are what they are.
          if (first || (error instanceof ApiError && ["network", "signed-out", "slow-down"].includes(error.kind))) throw error;
          throw new ApiError("backup-part");
        }
        onFile?.(files.length, total);
      };
      await take("/file", main, true);
      for (const n of parts) await take(`/file/${n}`, `${stem}.transcripts-${n}-of-${got.partsTotal}.dmbot.json.gz`, false);
      return { files };
    },
    takeBackHandover: async (ref) => {
      await call("DELETE", `/me/campaigns/${encodeURIComponent(ref)}/handover`);
    },
    handoverLook: async (token) => (await call("POST", "/me/handover/look", { token })) as HandoverLook,
    handoverAccept: async (token, serverId) => {
      const got = (await call("POST", "/me/handover/accept", { token, serverId })) as { status?: string; campaignRef?: string };
      return got.status === "moving" ? { moving: true as const } : { campaignRef: got.campaignRef ?? "" };
    },
    handoverDecline: async (token) => {
      await call("POST", "/me/handover/decline", { token });
    },
    removeDiscordAccount: async (key) => {
      await call("POST", `/me/discord-accounts/${encodeURIComponent(key)}/remove`);
    },
    getMoveCode: async () => {
      await call("POST", "/me/move-code");
    },
    myEmail: async () => (await call("GET", "/me/email")) as MyEmail,
    changeEmail: async (address) => (await call("PUT", "/me/email", { address })) as { pending: string | null },
    confirmEmail: async (token) => {
      await call("POST", "/me/email/confirm", { token });
    },
    cancelEmailChange: async () => {
      await call("DELETE", "/me/email/pending");
    },
    sendSupport: async (ticket) => {
      const answer = (await call("POST", "/support", ticket)) as {
        reply?: unknown;
      } | null;
      // Only an explicit "no address" changes the words: an answer without the field is a
      // ticket that was kept, and the team still replies by email.
      return { reply: answer?.reply !== false };
    },
    mySupport: async () => {
      const answer = (await call("GET", "/support/mine")) as {
        tickets?: unknown;
        reply?: unknown;
      } | null;
      // Anything that is not a list of questions is no list: the page shows nothing rather
      // than guess.
      const tickets = Array.isArray(answer?.tickets) ? answer.tickets : [];
      return {
        tickets: tickets.filter(
          (t): t is SupportQuestion =>
            typeof t === "object" &&
            t !== null &&
            typeof t.at === "number" &&
            typeof t.subject === "string" &&
            (t.status === "new" ||
              t.status === "answered" ||
              t.status === "escalated" ||
              t.status === "closed"),
        ),
        reply: answer?.reply !== false,
      };
    },
    supportThread: async (at) => {
      const answer = (await call("GET", `/support/mine/${encodeURIComponent(String(at))}`)) as {
        messages?: unknown;
      } | null;
      const parts = Array.isArray(answer?.messages) ? answer.messages : [];
      return parts.filter(
        (m): m is SupportMessage =>
          typeof m === "object" &&
          m !== null &&
          (m.who === "you" || m.who === "helper" || m.who === "team") &&
          typeof m.text === "string" &&
          typeof m.at === "number",
      );
    },
    signOutEverywhere: async () => {
      await call("POST", "/me/sign-out-everywhere");
    },
    startTryIt: async () => {
      await call("POST", "/plan/try-it");
    },
    checkoutUrl: (plan) => url("/billing/checkout", { plan }),
    billingPortalUrl: (flow) => url("/billing/portal", flow ? { flow } : undefined),
    renewPlan: async () => {
      await call("POST", "/billing/renew");
    },
    extraHoursUrl: () => url("/billing/extra-hours"),
    downloadExport: async () =>
      (await call("GET", "/me/export", undefined, true)) as Blob,
    characters: async () =>
      ((await call("GET", "/me/characters")) as { characters: Character[] })
        .characters,
    linkServer: async (serverId) => {
      await call("POST", `/servers/${encodeURIComponent(serverId)}/link`);
    },
    // No handoverCandidates or handover yet: the API has no route to make an offer from
    // the site until #704's follow-up, so the Hand over button stays hidden.
    acceptOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/accept`);
    },
    declineOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/decline`);
    },
    withdrawOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/withdraw`);
    },
    stopRecording: async (serverId) => {
      await call("POST", `/me/recording/${encodeURIComponent(serverId)}/stop`);
    },
    myLines: async (serverId) => (await call("GET", `/me/transcripts/${encodeURIComponent(serverId)}/mine`)) as MyLines,
    deleteMyLines: async (serverId) =>
      ((await call("POST", `/me/transcripts/${encodeURIComponent(serverId)}/delete`)) as { id: string }).id,
    myLinesStatus: async (serverId, id) =>
      (await call("GET", `/me/transcripts/${encodeURIComponent(serverId)}/delete/${encodeURIComponent(id)}`)) as MyLinesStatus,
    requestDelete: async () =>
      (
        (await call("POST", "/account/delete/request")) as {
          confirm_token: string;
        }
      ).confirm_token,
    confirmDelete: async (token) => {
      await call("POST", "/account/delete/confirm", { confirm_token: token });
    },
  };
}

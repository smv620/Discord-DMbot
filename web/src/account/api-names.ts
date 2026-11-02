// The "names" calls of the real API (#3010), fetched on demand by `httpApi` the first time a page uses one, so the
// pages that do not use them do not carry them. Each is the same call it was inside `httpApi`; the names are listed
// in `NAMES_METHODS` there, and a test keeps the two in step.
import type {
  CampaignName,
  ConfirmAnswer,
  MyNpcNote,
  NameAnswer,
  NameCheckList,
  NamesFixed,
  NamesPage,
  NpcPage,
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
import type { AccountApi, ApiHelpers, NodeSummary } from "./api";

export default function namesArea({ call, waitForAnswer }: ApiHelpers): Partial<AccountApi> {
  return {
    namesFixed: async (recapRef) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(recapRef)}/names-fixed`)) as NamesFixed,
    undoNameFix: async (recapRef, id) => {
      const queued = (await call("POST", `/me/recaps/${encodeURIComponent(recapRef)}/names-fixed/${encodeURIComponent(id)}/undo`, {})) as {
        answer: string;
      };
      if (!queued?.answer) throw new ApiError("server");
      // The bot's answer is polled under the campaign, as the report's are (the campaign is the recap's first two parts).
      const campaign = recapRef.split(".").slice(0, 2).join(".");
      const got = await waitForAnswer(`/me/campaigns/${encodeURIComponent(campaign)}/web/answers/${encodeURIComponent(queued.answer)}`);
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
    respellFromVoice: async (campaignRef, nameId, clip) =>
      (
        (await call(
          "POST",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as/from-voice`,
          undefined,
          false,
          clip,
        )) as { respelling: string }
      ).respelling,
    saveSaidAs: async (campaignRef, nameId, saidAs) => {
      await call(
        "PUT",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as`,
        { saidAs },
      );
    },
    nameChecks: async (ref) =>
      (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/names-to-check`)) as NameCheckList,
    answerNameCheck: async (ref, id, answer) => {
      await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/names-to-check/${encodeURIComponent(id)}`,
        answer,
      );
    },
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
      return (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/all-names?${params.toString()}`,
      )) as NamesPage;
    },
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
        onLook?.({ waits: look.waits ?? null, progress: look.progress ?? null }),
      );
      const kept = (got as { kept?: unknown }).kept;
      return typeof kept === "number" ? { kept } : {};
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
      return got.removed || !got.edge ? { removed: true } : got.edge;
    },
  };
}

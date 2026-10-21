/**
 * A pretend API for building and testing the account page before the real one (#435)
 * exists. Used by the tests, and in the browser only when the site is built with
 * PUBLIC_API_BASE=mock (then ?demo=<scenario> picks what to show). Never in production.
 *
 * The names are our own invented cast (docs/test-scripts/), never real people.
 */
import type { PlanId } from "../content/pricing";
import type {
  AccountApi,
  Campaign,
  Character,
  ImportProgress,
  Me,
  Offer,
  Person,
  CampaignName,
  Recap,
  RecapChoices,
  RecordingEntry,
  SupportQuestion,
  TranscriptVersion,
} from "./api";
import { ApiError } from "./api";

/** Found in the built files only if the pretend API was included; check-csp fails a real
 * build that contains it. */
export const MOCK_MARKER = "dmbot-pretend-api-7f3c";

export const scenarios = [
  "signed-out",
  "no-plan",
  "try-it",
  "table",
  "grace",
  "lapsed",
  "offers",
  "free",
  "free-until",
  "free-paying",
  "down",
] as const;
export type Scenario = (typeof scenarios)[number];

/** The ?demo= value if it names a scenario, otherwise "table". */
export function pickScenario(value: string | null): Scenario {
  return scenarios.find((s) => s === value) ?? "table";
}

const user: Person = { id: "100000000000000001", name: "Belleros" };

const campaigns: Campaign[] = [
  {
    id: "cmp-brynwater",
    name: "The Brynwater Crossing",
    serverName: "Thursday Table",
    lastPlayedAt: "2026-10-02T19:30:00Z",
    status: "active",
    role: "owner",
  },
  {
    id: "cmp-ashen",
    name: "Ashen Crown",
    serverName: "Gorrak's Hall",
    lastPlayedAt: null,
    status: "paused",
    role: "owner",
  },
  {
    id: "cmp-varrow",
    name: "Shrine of Varrow",
    serverName: "Thursday Table",
    lastPlayedAt: "2026-09-25T18:00:00Z",
    status: "active",
    role: "co-dm",
  },
];

const servers = [
  {
    id: "200000000000000001",
    name: "Thursday Table",
    hasDmbot: true,
    canLink: false,
    installedByYou: true,
  },
  {
    id: "200000000000000002",
    name: "Quillon's Corner",
    hasDmbot: false,
    canLink: false,
    installedByYou: false,
  },
  {
    id: "200000000000000003",
    name: "Brynwater Players",
    hasDmbot: true,
    canLink: true,
    installedByYou: false,
  },
];

export const candidates: Person[] = [
  { id: "100000000000000002", name: "Oskar Vane" },
  { id: "100000000000000003", name: "Mirelle" },
];

/** Hand-overs for the "offers" scenario (#614). "offer-full" is refused: no free slot. */
export const incomingOffers: Offer[] = [
  {
    id: "offer-ember",
    campaignId: "cmp-ember",
    campaignName: "Embers of Caldry",
    serverName: "Quillon's Corner",
    personName: "Oskar Vane",
    expiresAt: "2026-10-14T18:00:00Z",
  },
  {
    id: "offer-full",
    campaignId: "cmp-tidewatch",
    campaignName: "Tidewatch",
    serverName: "Brynwater Players",
    personName: "Mirelle",
    expiresAt: "2026-10-12T18:00:00Z",
  },
];

export const outgoingOffer: Offer = {
  id: "offer-ashen",
  campaignId: "cmp-ashen",
  campaignName: "Ashen Crown",
  serverName: "Gorrak's Hall",
  personName: "Mirelle",
  expiresAt: "2026-10-13T18:00:00Z",
};

export const characters: Character[] = [
  {
    serverName: "Thursday Table",
    campaign: "The Brynwater Crossing",
    name: "Testa",
    classLine: "Bard 2",
    level: 2,
    maxHp: 14,
    ac: 13,
    sheetLink: null,
    typedIn: true,
    readAt: "2026-10-02T19:30:00Z",
  },
  {
    serverName: "Gorrak's Hall",
    campaign: "Ashen Crown",
    name: "Brannoch",
    classLine: "Ranger 3 / Druid 3",
    level: 6,
    maxHp: 52,
    ac: 16,
    sheetLink: "https://sheets.example/characters/12345678",
    typedIn: false,
    readAt: "2026-10-03T10:00:00Z",
  },
];

export const recording: RecordingEntry[] = [
  {
    serverId: "srv-thursday",
    serverName: "Thursday Table",
    agreedOn: "2026-09-20",
    on: true,
  },
  {
    serverId: "srv-gorrak",
    serverName: "Gorrak's Hall",
    agreedOn: "2026-08-02",
    on: true,
  },
];

export function scenarioMe(scenario: Scenario): Me | null {
  const base = {
    user,
    campaigns,
    servers,
    recording,
    dmbotId: "1P0Z4F6N2K9",
    experiencePoints: 1245,
    canMove: false,
    canSupport: false,
    offers: { incoming: [], outgoing: [] },
  };
  switch (scenario) {
    case "signed-out":
    case "down":
      return null;
    case "no-plan":
      return { ...base, plan: null, campaigns: [] };
    case "try-it":
      return {
        ...base,
        canSupport: true,
        campaigns: campaigns.slice(0, 1),
        plan: {
          id: "try-it",
          status: "active",
          hoursUsed: 2.4,
          hoursCap: 8,
          renewsOn: "2026-11-01",
          graceEndsOn: null,
        },
      };
    case "table":
      return {
        ...base,
        canMove: true,
        canSupport: true,
        plan: {
          id: "table",
          status: "active",
          hoursUsed: 11.2,
          hoursCap: 18,
          renewsOn: "2026-10-14",
          graceEndsOn: null,
        },
      };
    case "grace":
      return {
        ...base,
        plan: {
          id: "two-tables",
          status: "grace",
          hoursUsed: 30,
          hoursCap: 43,
          renewsOn: "2026-10-14",
          graceEndsOn: "2026-10-12",
        },
      };
    case "lapsed":
      return {
        ...base,
        plan: {
          id: "table",
          status: "lapsed",
          hoursUsed: 0,
          hoursCap: 18,
          renewsOn: null,
          graceEndsOn: null,
        },
      };
    case "free":
      return { ...base, plan: null, access: { kind: "free" } };
    case "free-until":
      return {
        ...base,
        plan: null,
        access: { kind: "free", endsOn: "2026-12-31" },
      };
    case "free-paying":
      return {
        ...base,
        plan: {
          id: "table",
          status: "active",
          hoursUsed: 3,
          hoursCap: 18,
          renewsOn: "2026-10-14",
          graceEndsOn: null,
        },
        access: { kind: "free", stillPaying: true, paidPlan: "Table" },
      };
    case "offers":
      return {
        ...base,
        offers: { incoming: incomingOffers, outgoing: [outgoingOffer] },
        plan: {
          id: "two-tables",
          status: "active",
          hoursUsed: 6,
          hoursCap: 43,
          renewsOn: "2026-10-14",
          graceEndsOn: null,
        },
      };
  }
}

/** Two pretend recaps for the DM's review page (#1386): one ready, one posted. */
export function mockRecaps(): Recap[] {
  const base = {
    campaign: "The Brynwater Crossing",
    serverName: "Thursday Table",
    gameDay: null,
    state: "ready",
    working: false,
    error: "",
    audio: {
      state: "off",
      error: "",
      current: false,
      canAskAgain: false,
    } as const,
  };
  return [
    {
      ...base,
      ref: "200000000000000001.cmp1.1791000000",
      sessionAt: 1791000000,
      status: "draft",
      title: "The ferry at dusk",
      text: "The party crossed the Brynwater at dusk.\n\nThe ferryman asked for a story, not coin.",
      summary:
        "[music: calm] The party crossed the Brynwater at dusk.\n\nThe ferryman asked for a story, not coin.",
      housekeeping: [
        "Mira is down to 4 hit points.",
        "The party owes the ferryman a story.",
      ],
      toConfirm: ["Brynwater Ferry"],
      canEdit: true,
      canPost: true,
      canTryAgain: true,
      triesLeft: 2,
      canDrop: true,
      approval: { approved: false, choice: "", approvedAt: null, canApprove: true },
      share: { on: false, path: null },
    },
    {
      ...base,
      ref: "200000000000000001.cmp1.1790000000",
      share: { on: false, path: null },
      sessionAt: 1790000000,
      status: "posted",
      title: "The vault door",
      text: "The vault stayed shut.",
      summary: "The vault stayed shut.",
      housekeeping: [],
      canEdit: false,
      canPost: false,
      canTryAgain: false,
      triesLeft: 2,
      canDrop: false,
    },
  ];
}

/** A pretend API with in-memory state. `calls` records what the page asked for. */
export function mockApi(scenario: Scenario): AccountApi & { calls: string[] } {
  console.info(MOCK_MARKER, "pretend API in use:", scenario);
  let me = scenarioMe(scenario);
  // The questions sent in this visit (#1373): the pretend API starts with none, as a new account has.
  let questions: SupportQuestion[] = [];
  // "Use what I say and type to improve DMbot" (#1610): on until the person turns it off.
  let improve = true;
  const calls: string[] = [];
  const reachable = async (): Promise<void> => {
    if (scenario === "down") throw new ApiError("network");
  };
  const signedIn = (): Me => {
    if (!me) throw new ApiError("signed-out");
    return me;
  };

  const recaps = mockRecaps();
  // The DM's recap choices, as the API says them (#1468).
  const choices: RecapChoices = {
    text: true,
    audio: true,
    email: true,
    canAudio: true,
    audioHow: "part of your plan",
  };
  // The campaign's public names, as the API lists them (#1451).
  const names: CampaignName[] = [
    { id: "a".repeat(32), text: "Ka'zeth", kind: "full", saidAs: null },
    { id: "b".repeat(32), text: "Brynwater", kind: "full", saidAs: "Brin-water" },
  ];
  const audioAsked = new Set<string>(); // a recap asked for again once: the next ask is "too soon"
  let sharedCount = 0;
  const find = async (ref: string): Promise<Recap> => {
    await reachable();
    signedIn();
    const found = recaps.find((r) => r.ref === ref);
    if (!found) throw new ApiError("not-found");
    return found;
  };

  return {
    calls,
    async recaps() {
      calls.push("recaps");
      await reachable();
      signedIn();
      return recaps.map((r) => ({ ...r }));
    },
    async recap(ref) {
      calls.push(`recap:${ref}`);
      return { ...(await find(ref)) };
    },
    async saveRecap(ref, summary, housekeeping) {
      calls.push(`saveRecap:${ref}`);
      const r = await find(ref);
      if (!r.canEdit) throw new ApiError("not-editable");
      Object.assign(r, { summary, text: summary, housekeeping });
      return { ...r };
    },
    async approveRecap(ref, choice) {
      calls.push(`approveRecap:${ref}:${choice}`);
      const r = await find(ref);
      if (!r.approval?.canApprove) throw new ApiError("not-allowed");
      if (choice !== "text" && !choices.canAudio) throw new ApiError("not-in-plan");
      r.approval = { approved: true, choice, approvedAt: 1791000100, canApprove: false };
      return { ...r };
    },
    // Importing older sessions (#2107). Speakers are the "Name:" at the start of each line; Cerric
    // is suggested as Stormy (who agreed) and Ash has not agreed to be recorded.
    // A Google Doc link (#2140) is read as a file named "Google Doc N.txt". A link with "private" in
    // it is not shared, "notadoc" is not a Google Doc, and a link pasted twice is flagged the second time.
    async importPreview(ref, files, links = []) {
      calls.push(
        `importPreview:${ref}:${files.map((f) => f.name).join(",")}${links.length ? `|links:${links.length}` : ""}`,
      );
      const docs = links.map((link, i) => {
        const name = `Google Doc ${i + 1}.txt`;
        const error = links.indexOf(link) !== i || link.includes("repeat")
          ? "repeated_link"
          : link.includes("notadoc")
            ? "not_a_google_doc"
            : link.includes("private")
              ? "doc_not_shared"
              : link.includes("unreachable")
                ? "doc_unreachable"
                : link.includes("toobig")
                  ? "doc_too_big"
                  : null;
        return { name, text: error ? "" : "Cerric: Hello from the doc\nMira: And hello", error };
      });
      return {
        files: [...files, ...docs.map((d) => ({ name: d.name, text: d.text }))].map((f) => {
          const bad = docs.find((d) => d.name === f.name)?.error;
          if (bad)
            return { name: f.name, format: null, error: bad, suggestedDate: null, lineCount: 0, leftOut: 0, speakers: [] };
          if (!f.name.toLowerCase().endsWith(".txt"))
            return { name: f.name, format: null, error: "not_txt", suggestedDate: null, lineCount: 0, leftOut: 0, speakers: [] };
          const counts = new Map<string, number>();
          for (const line of f.text.split("\n")) {
            const who = /^([^:]{1,40}):/.exec(line)?.[1];
            if (who) counts.set(who, (counts.get(who) ?? 0) + 1);
          }
          const day = /(\d{4}-\d{2}-\d{2})/.exec(f.name)?.[1] ?? null;
          return {
            name: f.name,
            format: "plain",
            error: counts.size === 0 ? "empty" : null,
            suggestedDate: day,
            lineCount: [...counts.values()].reduce((a, b) => a + b, 0),
            leftOut: 0,
            speakers: [...counts].map(([label, lines]) => ({
              label,
              lines,
              suggested: label === "Cerric" ? { userId: "1", name: "Stormy", why: "remembered" } : null,
            })),
          };
        }),
        estimate: { sessions: files.length + links.length, fastCalls: 14 * (files.length + links.length) },
      };
    },
    async importMembers(ref, q) {
      calls.push(`importMembers:${ref}:${q}`);
      const all = [
        { userId: "1", name: "Stormy", agreed: true },
        { userId: "2", name: "Ash", agreed: false },
        { userId: "3", name: "Mira", agreed: true },
      ];
      return all.filter((m) => m.name.toLowerCase().includes(q.toLowerCase()));
    },
    async importCheck(ref, files, mapping, links = []) {
      calls.push(`importCheck:${ref}${links.length ? `|links:${links.length}` : ""}`);
      files = [
        ...files,
        ...links.map((_, i) => ({ name: `Google Doc ${i + 1}.txt`, text: "Cerric: Hello from the doc\nMira: And hello" })),
      ];
      const members: Record<string, boolean> = { "1": true, "2": false, "3": true };
      return {
        files: files.map((f) => {
          const counts = new Map<string, number>();
          for (const line of f.text.split("\n")) {
            const who = /^([^:]{1,40}):/.exec(line)?.[1];
            if (who) counts.set(who, (counts.get(who) ?? 0) + 1);
          }
          // A doc with "later" in its link opened at Preview but is not shared any more at Check.
          const lostDoc = links.some((l, i) => l.includes("later") && f.name === `Google Doc ${i + 1}.txt`);
          return {
            name: f.name,
            error: lostDoc ? "doc_not_shared" : null,
            leftOut: 0,
            speakers: [...counts].map(([label, n]) => {
              const to = mapping[label] ?? "drop";
              const kept = to !== "drop" && members[to] ? n : 0;
              return {
                label,
                kept,
                dropped: {
                  not_matched: to === "drop" ? n : 0,
                  not_agreed: to !== "drop" && !members[to] ? n : 0,
                  bot: 0,
                  left_out: 0,
                },
              };
            }),
          };
        }),
        estimate: { sessions: files.length, fastCalls: 14 * files.length },
      };
    },
    async importStart(ref, _files, plan, links = []) {
      calls.push(
        `importStart:${ref}:${plan.sessions.map((s) => `${s.order}=${s.file}@${s.date}${s.writeRecap ? "#recap" : ""}`).join(",")}${links.length ? `|links:${links.join(" ")}` : ""}`,
      );
      if (links.some((l) => l.includes("later"))) throw new ApiError("bad-files");
      return {
        importId: "imp1",
        sessions: plan.sessions.map((s) => ({ file: s.file, state: "waiting" as const })),
        estimate: { sessions: plan.sessions.length, fastCalls: 14 * plan.sessions.length },
      };
    },
    async importProgress(ref, importId) {
      calls.push(`importProgress:${ref}:${importId}`);
      const seen = calls.filter((c) => c.startsWith("importProgress:")).length;
      const state = seen < 2 ? ("working" as const) : ("done" as const);
      return {
        sessions: [
          {
            file: "session-1-2026-09-14.txt",
            date: "2026-09-14",
            state,
            reason: null,
            stage: state === "done" ? ("done" as const) : ("cleaning" as const),
          },
        ],
      };
    },
    async recapChoices(campaignRef) {
      calls.push(`recapChoices:${campaignRef}`);
      await reachable();
      signedIn();
      return { ...choices };
    },
    async setRecapChoice(campaignRef, which, on) {
      calls.push(`setRecapChoice:${campaignRef}:${which}:${on}`);
      await reachable();
      signedIn();
      if (which === "audio" && on && !choices.canAudio) throw new ApiError("not-in-plan");
      choices[which] = on;
      return { ...choices };
    },
    async transcriptCampaigns() {
      calls.push("transcriptCampaigns");
      await reachable();
      signedIn();
      return [
        {
          ref: "200000000000000001.cmp1",
          name: "The Brynwater Crossing",
          serverName: "Thursday Table",
          sessions: 2,
          keepDays: 90,
        },
      ];
    },
    async transcriptSessions(campaignRef) {
      calls.push(`transcriptSessions:${campaignRef}`);
      await reachable();
      signedIn();
      if (campaignRef !== "200000000000000001.cmp1") throw new ApiError("not-found");
      return {
        name: "The Brynwater Crossing",
        keepDays: 90,
        sessions: [
          {
            number: 3,
            startedAt: 1_792_000_000,
            endedAt: 1_792_003_600,
            lines: 0,
            transcriptsDeletedAt: 1_792_500_000,
          },
          { number: 2, startedAt: 1_791_000_000, endedAt: 1_791_007_200, lines: 3 },
          { number: 1, startedAt: 1_790_000_000, endedAt: 1_790_003_600, lines: 2 },
        ],
      };
    },
    async transcript(campaignRef, number, version) {
      calls.push(`transcript:${campaignRef}:${number}:${version}`);
      await reachable();
      signedIn();
      if (campaignRef !== "200000000000000001.cmp1" || ![1, 2, 3].includes(number))
        throw new ApiError("not-found");
      if (number === 3)
        return {
          number,
          version,
          fileName: "",
          notes: [],
          lines: [],
          next: null,
          transcriptsDeletedAt: 1_792_500_000,
        };
      return mockTranscript(number, version);
    },
    async transcriptFile(campaignRef, number, version) {
      calls.push(`transcriptFile:${campaignRef}:${number}:${version}`);
      await reachable();
      signedIn();
      const view = mockTranscript(number, version);
      return new Blob([[...view.notes, "", ...view.lines].join("\n") + "\n"], {
        type: "text/plain",
      });
    },
    async houseRuleCampaigns() {
      calls.push("houseRuleCampaigns");
      await reachable();
      signedIn();
      return [
        {
          ref: "200000000000000001.cmp1",
          name: "The Brynwater Crossing",
          serverName: "Thursday Table",
          rules: 2,
          sets: 1,
        },
      ];
    },
    async houseRules(campaignRef) {
      calls.push(`houseRules:${campaignRef}`);
      await reachable();
      signedIn();
      if (campaignRef !== "200000000000000001.cmp1") throw new ApiError("not-found");
      return mockHouseRules();
    },
    async houseRulesFile(campaignRef) {
      calls.push(`houseRulesFile:${campaignRef}`);
      await reachable();
      signedIn();
      const rules = mockHouseRules();
      return new Blob(
        [
          [
            `House rules: ${rules.name}`,
            ...[...rules.rules].reverse().map((r) => `${r.number}. ${r.rule}`),
          ].join("\n") + "\n",
        ],
        { type: "text/plain" },
      );
    },
    async improvement() {
      calls.push("improvement");
      await reachable();
      signedIn();
      return improve;
    },
    async setImprovement(on) {
      calls.push(`setImprovement:${on}`);
      await reachable();
      signedIn();
      improve = on;
      return improve;
    },
    async homebrewCampaigns() {
      calls.push("homebrewCampaigns");
      await reachable();
      signedIn();
      return [
        {
          ref: "200000000000000001.cmp1",
          name: "The Brynwater Crossing",
          serverName: "Thursday Table",
          entries: 5,
          packs: 1,
        },
      ];
    },
    async homebrew(campaignRef) {
      calls.push(`homebrew:${campaignRef}`);
      await reachable();
      signedIn();
      if (campaignRef !== "200000000000000001.cmp1") throw new ApiError("not-found");
      return mockHomebrew();
    },
    async homebrewFile(campaignRef) {
      calls.push(`homebrewFile:${campaignRef}`);
      await reachable();
      signedIn();
      const shown = mockHomebrew();
      return new Blob(
        [
          [`Homebrew: ${shown.name}`, ...shown.entries.map((e) => `## ${e.name} (${e.kind})`)].join(
            "\n",
          ) + "\n",
        ],
        { type: "text/plain" },
      );
    },
    async campaignNames(campaignRef) {
      calls.push(`campaignNames:${campaignRef}`);
      await reachable();
      signedIn();
      return names.map((n) => ({ ...n }));
    },
    async saveSaidAs(campaignRef, nameId, saidAs) {
      calls.push(`saveSaidAs:${campaignRef}:${nameId}:${saidAs ?? ""}`);
      await reachable();
      signedIn();
      const found = names.find((n) => n.id === nameId);
      if (!found) throw new ApiError("not-found");
      found.saidAs = saidAs === null || saidAs.trim() === "" ? null : saidAs.trim();
    },
    async recapAudioLinks(ref) {
      calls.push(`recapAudioLinks:${ref}`);
      await find(ref);
      // The real API sends none unless the audio matches the text; the page only asks then.
      return { voice: "#demo-voice", music: "#demo-music", mix: "#demo-mix" };
    },
    async askRecapAudioAgain(ref) {
      calls.push(`askRecapAudioAgain:${ref}`);
      const r = await find(ref);
      if (audioAsked.has(ref)) throw new ApiError("too-soon");
      audioAsked.add(ref);
      r.audio = {
        state: "queued",
        error: "",
        current: false,
        canAskAgain: false,
      };
      return { ...r };
    },
    // Sharing a posted recap by a link (#2119). Only a posted recap can have one.
    async shareRecap(ref, action) {
      calls.push(`shareRecap:${ref}:${action}`);
      const r = await find(ref);
      if (r.status !== "posted") throw new ApiError("not-posted");
      if (action === "off") r.share = { on: false, path: null };
      else if (action === "new" || !r.share?.on) {
        sharedCount += 1;
        r.share = { on: true, path: `/r/secret${sharedCount}` };
      }
      r.shared = r.share?.on ?? false;
      return { ...(r.share ?? { on: false, path: null }) };
    },
    async publicRecap(token) {
      calls.push(`publicRecap:${token}`);
      await reachable();
      const r = recaps.find((x) => x.share?.on && x.share.path === `/r/${token}`);
      if (!r) throw new ApiError("not-found");
      return { title: r.title, text: r.text, postedAt: r.sessionAt, audio: {} };
    },
    async decideRecap(ref, action) {
      calls.push(`${action}Recap:${ref}`);
      const r = await find(ref);
      if (action === "again" && !r.canTryAgain)
        throw new ApiError("not-allowed");
      if (action === "post")
        Object.assign(r, { status: "posting", canEdit: false, canPost: false });
      if (action === "drop")
        Object.assign(r, { status: "dropped", canEdit: false, canPost: false });
      if (action === "again") r.triesLeft -= 1;
      if (r.status !== "draft")
        Object.assign(r, { canTryAgain: false, canDrop: false });
      else r.canTryAgain = r.triesLeft > 0;
      return { ...r };
    },
    async me() {
      calls.push("me");
      await reachable();
      return me;
    },
    signInUrl: () => "#demo-sign-in",
    async signOutEverywhere() {
      calls.push("signOutEverywhere");
      me = null;
    },
    async signOut() {
      calls.push("signOut");
      me = null;
    },
    async emailSignInRequest(email) {
      calls.push(`email-request:${email}`);
    },
    async emailSignInUse(token) {
      calls.push(`email-use:${token}`);
      if (token !== "good-link") throw new ApiError("bad-link");
      me = scenarioMe("table");
    },
    async getMoveCode() {
      calls.push("move-code");
      signedIn();
    },
    async sendSupport(ticket) {
      calls.push("support");
      signedIn();
      questions = [
        {
          at: Math.floor(Date.now() / 1000),
          subject: ticket.subject,
          status: "new",
        },
        ...questions,
      ];
      return { reply: true };
    },
    async mySupport() {
      calls.push("my-support");
      signedIn();
      return { tickets: questions, reply: true };
    },
    async startTryIt() {
      calls.push("startTryIt");
      const now = signedIn();
      me = {
        ...now,
        plan: {
          id: "try-it",
          status: "active",
          hoursUsed: 0,
          hoursCap: 8,
          renewsOn: null,
          graceEndsOn: null,
        },
      };
    },
    async checkoutUrl(plan: PlanId) {
      calls.push(`checkout:${plan}`);
      return `#demo-checkout-${plan}`;
    },
    async billingPortalUrl() {
      calls.push("portal");
      return "#demo-billing";
    },
    async recapAudioUrl() {
      calls.push("recap-audio");
      return "#demo-recap-audio";
    },
    async extraHoursUrl() {
      calls.push("extra-hours");
      return "#demo-extra-hours";
    },
    async characters() {
      calls.push("characters");
      signedIn();
      return characters;
    },
    async downloadExport() {
      calls.push("export");
      return new Blob(['{"about":"pretend export"}'], {
        type: "application/json",
      });
    },
    installUrl: (serverId: string) => `#demo-install-${serverId}`,
    async linkServer(serverId: string) {
      calls.push(`link:${serverId}`);
      const now = signedIn();
      me = {
        ...now,
        servers: now.servers.map((s) =>
          s.id === serverId
            ? { ...s, canLink: false, installedByYou: true }
            : s,
        ),
      };
    },
    async handoverCandidates(campaignId: string) {
      calls.push(`candidates:${campaignId}`);
      return campaignId === "cmp-ashen" ? [] : candidates;
    },
    async handover(campaignId: string, toUserId: string) {
      // Hand-over is an offer the other person accepts (#437): the campaign stays yours
      // until then, with the offer and Withdraw on its row.
      calls.push(`handover:${campaignId}:${toUserId}`);
      const now = signedIn();
      const campaign = now.campaigns.find((c) => c.id === campaignId);
      const person = candidates.find((p) => p.id === toUserId);
      if (!campaign || !person) throw new ApiError("not-allowed");
      const offer: Offer = {
        id: `offer-${campaignId}`,
        campaignId,
        campaignName: campaign.name,
        serverName: campaign.serverName,
        personName: person.name,
        expiresAt: "2026-10-15T18:00:00Z",
      };
      me = {
        ...now,
        offers: { ...now.offers, outgoing: [...now.offers.outgoing, offer] },
      };
    },
    async acceptOffer(offerId: string) {
      calls.push(`accept:${offerId}`);
      const now = signedIn();
      if (offerId === "offer-full") throw new ApiError("no-free-slot");
      const offer = now.offers.incoming.find((o) => o.id === offerId);
      if (!offer) throw new ApiError("offer-gone");
      const taken: Campaign = {
        id: offer.campaignId,
        name: offer.campaignName,
        serverName: offer.serverName,
        lastPlayedAt: null,
        status: "active",
        role: "owner",
      };
      me = {
        ...now,
        campaigns: [...now.campaigns, taken],
        offers: {
          ...now.offers,
          incoming: now.offers.incoming.filter((o) => o.id !== offerId),
        },
      };
    },
    async declineOffer(offerId: string) {
      calls.push(`decline:${offerId}`);
      const now = signedIn();
      me = {
        ...now,
        offers: {
          ...now.offers,
          incoming: now.offers.incoming.filter((o) => o.id !== offerId),
        },
      };
    },
    async stopRecording(serverId: string) {
      calls.push(`stop-recording:${serverId}`);
      const now = signedIn();
      me = {
        ...now,
        recording: (now.recording ?? []).filter((r) => r.serverId !== serverId),
      };
    },
    async withdrawOffer(offerId: string) {
      calls.push(`withdraw:${offerId}`);
      const now = signedIn();
      me = {
        ...now,
        offers: {
          ...now.offers,
          outgoing: now.offers.outgoing.filter((o) => o.id !== offerId),
        },
      };
    },
    async requestDelete() {
      calls.push("requestDelete");
      return "demo-token";
    },
    async confirmDelete(token: string) {
      calls.push(`confirmDelete:${token}`);
      me = null;
    },
  };
}

/** A pretend transcript in the shape the API gives: the file's header lines and its lines. */
function mockTranscript(number: number, version: TranscriptVersion) {
  const heard = version === "heard";
  return {
    number,
    version,
    fileName: `the-brynwater-crossing-session-${number}-${heard ? "as-heard" : "cleaned"}.txt`,
    notes: [
      `DMbot transcript: The Brynwater Crossing, session ${number}`,
      "Started 2026-10-02 19:30 UTC, ran 1 h",
      heard
        ? "As heard: every word exactly as DMbot heard it."
        : "Cleaned: DMbot fixed the spelling of names it was sure about.",
    ],
    lines: [
      "[0:00:12] (Speaker 1) {Cerric}: We cross the river at dusk.",
      heard
        ? "[0:00:40] (Speaker 2): Kazeth draws his sword."
        : "[0:00:40] (Speaker 2): Ka'zeth draws his sword.",
      ...(heard ? ["[0:01:02] (Speaker 1) {non-game_content}: did you see the match"] : []),
    ],
  };
}

/** Pretend house rules in the shape the API gives: the campaign's own, newest number first, then a
 * followed set's. */
function mockHouseRules() {
  return {
    name: "The Brynwater Crossing",
    fileName: "house-rules-the-brynwater-crossing.txt",
    rules: [
      {
        number: 2,
        rule: "Potions are a bonus action",
        supersedes: "Drinking a potion takes an action",
        fromSet: null,
      },
      { number: 1, rule: "Crits double the dice", supersedes: null, fromSet: null },
    ],
    sets: [
      {
        name: "Red Table",
        version: 4,
        cite: "Red Table house rules, v4",
        rules: [{ number: 1, rule: "Inspiration stacks", supersedes: null }],
      },
    ],
  };
}

/** Pretend shared homebrew in the shape the API gives: by name, the page groups it by kind. */
function mockHomebrew() {
  return {
    name: "The Brynwater Crossing",
    fileName: "homebrew-the-brynwater-crossing.txt",
    packs: [
      {
        name: "Frost Pack",
        version: 2,
        entries: [
          { kind: "spell", name: "Ice Lance", summary: "A cold bolt", text: "Cold damage." },
          { kind: "item", name: "Aurora Cloak", summary: null, text: "Shimmers." },
        ],
      },
    ],
    entries: [
      { kind: "spell", name: "Frost Lance", summary: "A bolt of ice", text: "A spear of ice hits a target within 60 feet for 3d6 cold damage." },
      { kind: "item", name: "Moon Lamp", summary: null, text: "Glows silver at night and never needs oil.", fromBook: true, legacy: false, source: "from dndbeyond.com" },
      { kind: "monster", name: "Mud Wight", summary: "Slow and sticky", text: "Slows anyone it touches.", fromBook: true, legacy: true },
    ],
  };
}

/** One imported session in each stage the API can report (#2230), for the page's tests and demos:
 * the last two are the old shape, with no stage. */
export const everyImportStage: ImportProgress = {
  sessions: [
    ...(
      [
        "cleaning",
        "checking_names",
        "finding_names",
        "waiting_for_names",
        "starting_recap",
        "writing_recap",
        "recap_ready",
        "recap_failed",
        "recap_not_written",
        "done",
        "failed",
      ] as const
    ).map((stage, i) => ({
      file: `${stage}.txt`,
      date: `2026-09-${String(i + 1).padStart(2, "0")}`,
      state: stage === "done" || stage.startsWith("recap_") ? ("done" as const) : stage === "failed" ? ("failed" as const) : ("working" as const),
      reason: null,
      stage,
      namesToAnswer: stage === "waiting_for_names" ? 3 : 0,
      namesUnchecked: false,
    })),
  ],
};

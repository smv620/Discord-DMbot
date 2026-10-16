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
  Me,
  Offer,
  Person,
  Recap,
  RecordingEntry,
  SupportQuestion,
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
      canEdit: true,
      canPost: true,
      canTryAgain: true,
      triesLeft: 2,
      canDrop: true,
    },
    {
      ...base,
      ref: "200000000000000001.cmp1.1790000000",
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
  const calls: string[] = [];
  const reachable = async (): Promise<void> => {
    if (scenario === "down") throw new ApiError("network");
  };
  const signedIn = (): Me => {
    if (!me) throw new ApiError("signed-out");
    return me;
  };

  const recaps = mockRecaps();
  const audioAsked = new Set<string>(); // a recap asked for again once: the next ask is "too soon"
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

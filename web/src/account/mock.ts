/**
 * A pretend API for building and testing the account page before the real one (#435)
 * exists. Used by the tests, and in the browser only when the site is built with
 * PUBLIC_API_BASE=mock (then ?demo=<scenario> picks what to show). Never in production.
 *
 * The names are our own invented cast (docs/test-scripts/), never real people.
 */
import type { PlanId } from "../content/pricing";
import type { AccountApi, Campaign, Me, Offer, Person } from "./api";
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

export function scenarioMe(scenario: Scenario): Me | null {
  const base = { user, campaigns, servers, offers: { incoming: [], outgoing: [] } };
  switch (scenario) {
    case "signed-out":
    case "down":
      return null;
    case "no-plan":
      return { ...base, plan: null, campaigns: [] };
    case "try-it":
      return {
        ...base,
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

/** A pretend API with in-memory state. `calls` records what the page asked for. */
export function mockApi(scenario: Scenario): AccountApi & { calls: string[] } {
  console.info(MOCK_MARKER, "pretend API in use:", scenario);
  let me = scenarioMe(scenario);
  const calls: string[] = [];
  const reachable = async (): Promise<void> => {
    if (scenario === "down") throw new ApiError("network");
  };
  const signedIn = (): Me => {
    if (!me) throw new ApiError("signed-out");
    return me;
  };

  return {
    calls,
    async me() {
      calls.push("me");
      await reachable();
      return me;
    },
    signInUrl: () => "#demo-sign-in",
    async signOut() {
      calls.push("signOut");
      me = null;
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
    installUrl: (serverId: string) => `#demo-install-${serverId}`,
    async linkServer(serverId: string) {
      calls.push(`link:${serverId}`);
      const now = signedIn();
      me = {
        ...now,
        servers: now.servers.map((s) =>
          s.id === serverId ? { ...s, canLink: false, installedByYou: true } : s,
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
      me = { ...now, offers: { ...now.offers, outgoing: [...now.offers.outgoing, offer] } };
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
        offers: { ...now.offers, incoming: now.offers.incoming.filter((o) => o.id !== offerId) },
      };
    },
    async declineOffer(offerId: string) {
      calls.push(`decline:${offerId}`);
      const now = signedIn();
      me = {
        ...now,
        offers: { ...now.offers, incoming: now.offers.incoming.filter((o) => o.id !== offerId) },
      };
    },
    async withdrawOffer(offerId: string) {
      calls.push(`withdraw:${offerId}`);
      const now = signedIn();
      me = {
        ...now,
        offers: { ...now.offers, outgoing: now.offers.outgoing.filter((o) => o.id !== offerId) },
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

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
  MyEmail,
  NameChanged,
  NameChange,
  NameKind,
  NameRow,
  NamesPage,
  NamesQuery,
  Offer,
  Person,
  CampaignName,
  Recap,
  RecapChoices,
  RecordingEntry,
  SupportQuestion,
  TranscriptVersion,
  WebEdge,
  WebGraph,
  WebGroup,
  WebLink,
  WebLinkChange,
  WebNode,
  WebQuery,
  CampaignPage,
  CampaignSettingAnswer,
  CampaignSettingName,
  Timeline,
  WebRevealTarget,
  Waiting,
  MyNpcNote,
  NpcPage,
} from "./api";
import { ApiError } from "./api";
import { NAME_LIMIT } from "../content/names-words";

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
  "lost-server",
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
    ref: "100.cmp1",
    name: "The Brynwater Crossing",
    serverName: "Thursday Table",
    lastPlayedAt: "2026-10-02T19:30:00Z",
    status: "active",
    role: "owner",
    // The card's facts (#2460).
    live: true,
    sessions: 12,
    hoursPlayed: 41.5,
    players: 4,
    npcs: 27,
    startedAt: "2026-03-14T18:00:00Z",
    // A test server: importing is on here though the site's own switch is off (#2260).
    importsOn: true,
  },
  {
    id: "cmp-ashen",
    ref: "100.cmp2",
    name: "Ashen Crown",
    serverName: "Gorrak's Hall",
    lastPlayedAt: null,
    status: "paused",
    role: "owner",
  },
  {
    id: "cmp-varrow",
    ref: "100.cmp3.cp-co",
    name: "Shrine of Varrow",
    serverName: "Thursday Table",
    lastPlayedAt: "2026-09-25T18:00:00Z",
    status: "active",
    role: "co-dm",
    sessions: 3,
    hoursPlayed: 9,
    players: 3,
  },
  // A campaign the person plays in (#2460): a card with no DM tools.
  {
    id: "cmp-lantern",
    ref: "100.cmp4",
    name: "The Lantern Road",
    serverName: "Thursday Table",
    lastPlayedAt: "2026-09-18T18:00:00Z",
    status: "active",
    role: "player",
    sessions: 5,
    hoursPlayed: 15.5,
    players: 5,
    npcs: 8,
    startedAt: "2026-06-01T18:00:00Z",
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
    discordAccounts: [
      { key: "d1", name: "Mira (main)", current: true, canRemove: true },
      { key: "d2", name: "Mira on the phone", current: false, canRemove: true },
    ],
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
    case "lost-server":
      // The table plan, plus a campaign of yours on a server DMbot can't reach any more (#2555).
      return {
        ...base,
        canMove: true,
        canSupport: true,
        campaigns: [
          ...campaigns,
          { id: "cmp-hollow", ref: "300.cmp9", name: "Lost Hollow", serverName: "", lastPlayedAt: null, status: "paused", role: "owner", serverReachable: false },
        ],
        plan: { id: "table", status: "active", hoursUsed: 11.2, hoursCap: 18, renewsOn: "2026-10-14", graceEndsOn: null },
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
    recapEmail: { address: "dm@example.com", source: "signin", on: true } as const,
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
      sessionNumber: 4,
      sessionSeconds: 2 * 3600 + 35 * 60,
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
    // Written but unchecked for 3 days (#2297): the draft to read, one sentence marked.
    {
      ...base,
      ref: "200000000000000001.cmp9.1789000000",
      campaign: "The Quiet Choir",
      share: { on: false, path: null },
      sessionAt: 1789000000,
      status: "draft",
      state: "prose",
      working: true,
      waitingForCheck: true,
      title: "",
      text: "",
      summary: "",
      housekeeping: [],
      canEdit: false,
      canPost: false,
      canTryAgain: false,
      triesLeft: 2,
      canDrop: true,
      uncheckedSince: 1788000000,
      uncheckedDraft: [
        "The party reached the river at dusk.",
        "Mira kept quiet because she feared the ferryman.",
        "They crossed after he asked for a story.",
      ],
      uncheckedSentences: [2],
      canApproveUnchecked: true,
    },
  ];
}

/** What the import preview and start say about recaps (#2229): a campaign whose address ends in
 * `.recaps-off`, `.recaps-campaign_off`, `.recaps-plan` or `.recaps-unknown` gets that case, any
 * other can have recaps. */
function recapFacts(ref: string): { recapsPossible: boolean; recapsWhy?: "off" | "campaign_off" | "plan" } {
  const why = /\.recaps-([a-z_]+)$/.exec(ref)?.[1];
  if (why === "unknown") return { recapsPossible: false };
  if (why === "off" || why === "campaign_off" || why === "plan") return { recapsPossible: false, recapsWhy: why };
  return { recapsPossible: true };
}


/** The pretend campaign behind the Names page (#2295): about fifty names across the kinds, some secret,
 * some with other names. Which view comes back depends on the campaign's address: ending in
 * `.names-manager` is a server manager who isn't a DM of it (no secret names, nothing to change), and
 * `.names-none` is someone who may not see the list (not found). Any other address is a DM. */
const NAME_KINDS: { key: string; label: string }[] = [
  { key: "npc", label: "People" },
  { key: "place", label: "Places" },
  { key: "group", label: "Groups" },
  { key: "item", label: "Items" },
  { key: "event", label: "Events" },
];

function startNames(): NameRow[] {
  const people = ["Mira Voss", "Aldric Thorn", "Sister Ilse", "Captain Brask", "Old Pell", "Tamsin Reed", "Gorrak", "The Ferryman", "Lady Orsolya", "Dunmar", "Hobb Fenwick", "Yselle", "Corvin", "Brother Anselm", "Nadia Quill"];
  const places = ["Brynwater Crossing", "The Gilded Hook", "Ashen Crown Keep", "Varrow Shrine", "Saltmarsh", "Greywatch", "The Low Docks", "Thorn Hollow", "Windmere"];
  const groups = ["The Tide Wardens", "Red Lantern Guild", "House Orsolya", "The Quiet Choir", "Brask's Company"];
  const items = ["The Brass Compass", "Voss's Ledger", "Shrine Key", "Stormglass", "The Ferrywoman's Token"];
  const events = ["The Night of Lanterns", "The Flooding", "The Council at Greywatch"];
  const rows: NameRow[] = [];
  const add = (kind: string, names: string[], npc: boolean): void => {
    for (const name of names) {
      const n = rows.length + 1;
      rows.push({
        id: `n${n}`,
        name,
        kind,
        others: n % 4 === 0 ? [`${name.split(" ")[0]}`, `${name} the Elder`] : n % 7 === 0 ? [`${name.split(" ").pop()}`] : [],
        secret: n % 9 === 0,
        npcId: npc ? `n${n}` : null,
        mentions: ((n * 7) % 11) + 1,
      });
    }
  };
  add("npc", people, true);
  add("place", places, false);
  add("group", groups, false);
  add("item", items, false);
  add("event", events, false);
  // A long name and a long unbroken word, for the layout checks.
  rows.push({ id: `n${rows.length + 1}`, name: "Ser Alaric-of-the-Many-Hills-and-Rivers-Beyond-the-Greywatch-Wall", kind: "npc", others: ["Alaric-of-the-Many-Hills-and-Rivers-Beyond-the-Greywatch-Wall-Elder-Brother"], secret: false, npcId: `n${rows.length + 1}` });
  return rows;
}

function namesView(ref: string): "dm" | "manager" | "none" {
  if (/\.names-none$/.test(ref)) return "none";
  if (/\.names-manager$/.test(ref)) return "manager";
  return "dm";
}

/** The pretend campaign behind the relationship web (#2324): the Names page's names joined by a few
 * made-up connections, so every ring has something in it. `.web-big` makes a campaign of 350 names
 * (the page warns before drawing it), `.web-manager` a server manager who isn't a DM (no secrets, no
 * editing) and `.web-none` someone who may not see it (not found). */
interface WebState {
  nodes: WebNode[];
  /** Each connection belongs to a pair (a, b) and has an order: later is newer. */
  links: {
    id: string;
    a: string;
    b: string;
    words: string;
    secret: boolean;
    direction: WebLink["direction"];
    revealed?: boolean;
    belief?: WebLink["belief"];
    pinned?: boolean;
  }[];
  nextLink: number;
}

function startWeb(big: boolean, lonely = false): WebState {
  const base = startNames();
  const nodes: WebNode[] = base.map((n) => ({ id: n.id, name: n.name, kind: n.kind, secret: n.secret }));
  if (big) {
    for (let i = nodes.length; i < 350; i += 1) nodes.push({ id: `n${i + 1}`, name: `Villager ${i + 1}`, kind: "npc", secret: false });
  }
  const words = ["works with", "owes a debt to", "is afraid of", "guards", "trusts", "serves", "is hunting", "grew up with"];
  const links: WebState["links"] = [];
  const connect = (a: number, b: number, secret = false): void => {
    if (a === b || a >= nodes.length || b >= nodes.length) return;
    const id = `l${links.length + 1}`;
    links.push({ id, a: nodes[a]!.id, b: nodes[b]!.id, words: words[links.length % words.length]!, secret, direction: links.length % 5 === 0 ? "both" : "ab" });
  };
  for (let i = 0; i < nodes.length; i += 1) {
    connect(i, (i + 1) % nodes.length);
    if (i % 3 === 0) connect(i, (i * 7 + 3) % nodes.length, i % 9 === 0);
  }
  // A big campaign has hubs everyone is tied to, so a few steps out reach nearly every name.
  if (big) for (let i = 12; i < nodes.length; i += 1) connect(i, i % 40);
  // Two connections between one pair, one of them secret: the line says "(+1)".
  connect(0, 1, true);
  connect(0, 1);
  // Not revealed to the players yet (#2405): two names and one connection that comes from a backstory.
  for (const i of [3, 8]) if (nodes[i]) nodes[i]!.revealed = false;
  const byId = (id: string) => links.find((l) => l.id === id);
  // One connection the DM pinned as 100% truth (#2448).
  const l2 = byId("l2");
  if (l2) l2.pinned = true;
  const l4 = byId("l4");
  if (l4) l4.revealed = false;
  // Theories the party holds: one it is unsure of, one it is wrong about (full view marks that).
  // Two ordinary connections between names everyone can see, so a player's view has them too.
  const plain = links.filter((l) => {
    const ends = [l.a, l.b].map((id) => nodes.find((n) => n.id === id));
    const pairs = links.filter((o) => (o.a === l.a && o.b === l.b) || (o.a === l.b && o.b === l.a));
    return !l.secret && l.revealed !== false && pairs.length === 1 && ends.every((n) => n && !n.secret && n.revealed !== false);
  });
  // The first the party is wrong about (full view says so); the second is only a hunch.
  if (plain[0]) plain[0].belief = { confidence: 0.6, status: "Party theory", contradicted: true };
  if (plain[1]) plain[1].belief = { confidence: 0.3, status: "Accusation" };
  // A young campaign (#2632): most names have no connection yet, and a second small group stands apart.
  if (lonely) {
    const extra: WebNode[] = [
      { id: "lone-mill", name: "Old Mill", kind: "place", secret: false },
      { id: "lone-tree", name: "Hollow Tree", kind: "place", secret: false },
      { id: "lone-venn", name: "Brother Venn", kind: "npc", secret: false },
      { id: "lone-wardens", name: "The Wardens", kind: "group", secret: false },
      { id: "pair-brask", name: "Brask", kind: "npc", secret: false },
      { id: "pair-ford", name: "Tolle Ford", kind: "place", secret: false },
    ];
    nodes.push(...extra);
    links.push({ id: "lp1", a: "pair-brask", b: "pair-ford", words: "lives at", secret: false, direction: "ab" });
  }
  return { nodes, links, nextLink: links.length + 1 };
}

function webView(ref: string): "dm" | "manager" | "none" {
  if (/\.web-none$/.test(ref)) return "none";
  if (/\.web-(manager|player|private|open)$/.test(ref)) return "manager"; // not a DM
  return "dm";
}

/** Who may see the DM screen's things in a pretend campaign (#2405): `.web-private` is the DM only,
 * `.web-open` everyone, anything else lets players peek. */
function webVisibility(ref: string): "private" | "peek" | "open" {
  return /\.web-private$/.test(ref) ? "private" : /\.web-open$/.test(ref) ? "open" : "peek";
}

/** A pretend API with in-memory state. `calls` records what the page asked for. */
/** The pretend list of what waits for the DM, a fresh copy for each campaign the page opens. */
function pretendWaiting(): Waiting {
  return {
    claims: [
      { id: "c1", statement: "Mira Voss knows Far Hall", canSecret: true, at: 1791000000 },
      { id: "c2", statement: "Roan is dead", canSecret: false },
    ],
    backstory: [
      { id: "b1", subject: "Rook", object: "Far Hall", how: "knows", secret: false, upload: "Lore, 24 Oct", ready: true, waitingOn: [], quote: "Rook has been to Far Hall." },
      { id: "b2", subject: "Rook", object: "Mira Voss", how: "grew up with", secret: true, upload: "Lore, 24 Oct", ready: true, waitingOn: [] },
      { id: "b3", subject: "Rook", object: "Newcomer", how: "knows", secret: false, upload: "Lore, 24 Oct", ready: false, waitingOn: ["Newcomer"] },
      { id: "b4", subject: "Mira Voss", object: "Harrow's Keep", how: "grew up in", secret: false, upload: "", ready: true, waitingOn: [] },
    ],
    which: [
      {
        id: "w1", word: "Mara", other: "Rook", otherIsSubject: true, how: "knows", secret: false, upload: "Lore, 24 Oct",
        candidates: [
          { id: "e1", name: "Mara Ashgrove", limited: false },
          { id: "e2", name: "Mara Vell", limited: true },
        ],
        quote: "Rook knows Sister Mara",
      },
    ],
    counts: { claims: 0, backstory: 0, ready: 0, which: 0, all: 0 },
  };
}
export function mockApi(scenario: Scenario): AccountApi & { calls: string[] } {
  const waitingState: Record<string, Waiting> = {}; // (fresh for each pretend API)
  console.info(MOCK_MARKER, "pretend API in use:", scenario);
  let me = scenarioMe(scenario);
  let putBackNow = false; // session 2's left-out stretch has been put back by a DM (#2568)
  const myEmail: MyEmail = {
    address: "dm@example.com",
    source: scenario === "table" ? "payment" : "signin",
    pending: null,
  };
  const memory = startNames();
  // How each listed name is said (#2317), by the name's id; the id the said-as calls take is `al-<id>`.
  const memorySaid = new Map<string, string | null>();
  /** A name for the said-as calls: a recap page's name, or a main name from the Names page. */
  const sayable = (id: string): { text: string; set: (v: string | null) => void } | undefined => {
    const recap = names.find((n) => n.id === id);
    if (recap) return { text: recap.text, set: (v) => void (recap.saidAs = v) };
    const row = memory.find((n) => `al-${n.id}` === id);
    return row ? { text: row.name, set: (v) => void memorySaid.set(row.id, v) } : undefined;
  };
  // My private notes on NPCs, by entry id (#2266). "old" is one on a name no longer listed.
  const npcNotes = new Map<string, string>([["old", "He owed me a favour."]]);
  // One pretend web per campaign address, started on first use.
  const webs = new Map<string, WebState>();
  const web = (ref: string): WebState => {
    let found = webs.get(ref);
    if (!found) webs.set(ref, (found = startWeb(/\.web-big$/.test(ref), /\.web-lonely$/.test(ref))));
    return found;
  };
  /** Edges from connections: one per pair, labelled with the newest known connection (the newest at all
   * if every one is secret and the viewer may see them). */
  const edgesOf = (links: WebState["links"], dm: boolean, full: boolean, pins = false): WebEdge[] => {
    const pairs = new Map<string, WebState["links"]>();
    for (const l of links) {
      if (l.secret && !dm) continue;
      const key = [l.a, l.b].sort().join("|");
      (pairs.get(key) ?? pairs.set(key, []).get(key)!).push(l);
    }
    return [...pairs.values()].map((group) => {
      const known = group.filter((l) => !l.secret);
      const label = (known.length > 0 ? known : group).at(-1)!.words;
      return {
        a: group[0]!.a,
        b: group[0]!.b,
        label,
        more: group.length - 1,
        links: group.map((l) => ({
          id: l.id,
          words: l.words,
          secret: l.secret,
          direction: l.direction,
          ...(full ? { revealed: l.revealed !== false } : {}),
          // The DM's alone (#2448): sent to a DM in the full view, never to a player or a peeker.
          ...(pins && l.pinned ? { pinned: true } : {}),
          ...(l.belief ? { belief: full ? l.belief : { confidence: l.belief.confidence, status: l.belief.status } } : {}),
        })),
      };
    });
  };
  const webGraphOf = (ref: string, query: WebQuery): WebGraph => {
    const view = webView(ref);
    if (view === "none") throw new ApiError("not-found");
    const dm = view === "dm";
    const visibility = webVisibility(ref);
    // What the person is shown (#2405): a DM sees everything unless they ask for the players' view; a
    // player sees the players' view unless they peek, which the campaign's DM-screen setting allows or not.
    if (!dm && query.view === "full" && visibility === "private") throw new ApiError("peek-off");
    const all = dm ? query.view !== "players" : query.view === "full";
    const state = web(ref);
    const visible = new Set(
      state.nodes.filter((n) => all || (!n.secret && n.revealed !== false)).map((n) => n.id),
    );
    const usable = state.links.filter(
      (l) => visible.has(l.a) && visible.has(l.b) && (all || (!l.secret && l.revealed !== false)),
    );
    const centre = query.center && visible.has(query.center) ? query.center : [...visible][0]!;
    // Hops over every usable connection, as the real API counts them.
    const near = new Map<string, number>([[centre, 0]]);
    const queue = [centre];
    for (let i = 0; i < queue.length; i += 1) {
      const here = queue[i]!;
      if ((near.get(here) ?? 0) >= query.hops) continue;
      for (const l of usable) {
        const there = l.a === here ? l.b : l.b === here ? l.a : null;
        if (there !== null && !near.has(there)) {
          near.set(there, (near.get(here) ?? 0) + 1);
          queue.push(there);
        }
      }
    }
    // A name changed on the Names routes (rename, kind) shows here too: they are the same names.
    const current = (n: WebNode): WebNode => {
      const live = memory.find((m) => m.id === n.id);
      return { ...n, name: live?.name ?? n.name, kind: live?.kind ?? n.kind };
    };
    const nodes = state.nodes
      .filter((n) => near.has(n.id))
      .map((n) =>
        all
          ? { ...current(n), revealed: n.revealed !== false }
          : { id: n.id, name: current(n).name, kind: current(n).kind },
      );
    const edges = edgesOf(usable.filter((l) => near.has(l.a) && near.has(l.b)), all, all, view === "dm" && all);
    return {
      center: centre,
      nodes,
      edges,
      truncated: false,
      maxHops: 6,
      canEdit: dm && all,
      view: all ? "full" : "players",
      ...(dm ? {} : { visibility, canPeek: visibility !== "private" }),
    };
  };
  // The questions sent in this visit (#1373): the pretend API starts with none, as a new account has.
  let questions: SupportQuestion[] = [];
  // "Use what I say and type to improve DMbot" (#1610): on until the person turns it off.
  let improve = true;
  const calls: string[] = [];
  const lateLooks = new Map<string, number>();
  const settingAnswers = new Map<string, { ref: string; setting: CampaignSettingName; value: string; confirm?: boolean }>();
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
    audioUnavailable: "",
  };
  // The campaign's public names, as the API lists them (#1451).
  const names: CampaignName[] = [
    { id: "a".repeat(32), text: "Ka'zeth", kind: "full", saidAs: null },
    { id: "b".repeat(32), text: "Brynwater", kind: "full", saidAs: "Brin-water" },
  ];
  const audioAsked = new Set<string>(); // a recap asked for again once: the next ask is "too soon"
  const fixLooks = new Map<string, number>();
  const voiceLooks = new Map<string, { looks: number; size: number }>();
  let sharedCount = 0;
  // A recap asked to try again is "being made" for one more read, then ready (the real API takes
  // minutes; the page polls). The page must show the working state at once, not the old failure.
  const writing = new Map<string, number>();
  const settle = (r: Recap): void => {
    writing.delete(r.ref);
    Object.assign(r, { working: false, state: "ready", canTryAgain: r.status === "draft" && r.triesLeft > 0 });
  };
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
      const r = await find(ref);
      const left = writing.get(ref);
      if (left !== undefined) {
        if (left <= 1) settle(r);
        else writing.set(ref, left - 1);
      }
      return { ...r };
    },
    async saveRecap(ref, summary, housekeeping) {
      calls.push(`saveRecap:${ref}`);
      const r = await find(ref);
      if (!r.canEdit) throw new ApiError("not-editable");
      Object.assign(r, { summary, text: summary, housekeeping });
      return { ...r };
    },
    async approveUnchecked(ref) {
      calls.push(`approveUnchecked:${ref}`);
      const r = await find(ref);
      if (!r.canApproveUnchecked) throw new ApiError("not-allowed");
      // As the bot would: the draft is finished as it stands, to edit and approve as usual.
      const text = (r.uncheckedDraft ?? []).join(" ");
      Object.assign(r, {
        working: false, waitingForCheck: false, state: "ready", text, summary: text, title: "Session recap",
        canEdit: true, canPost: true, canTryAgain: true, uncheckedSince: 0, uncheckedDraft: [],
        uncheckedSentences: [], canApproveUnchecked: false,
        approval: { approved: false, choice: "", approvedAt: null, canApprove: true },
      });
      return { ...r };
    },
    async approveRecap(ref, choice, options) {
      calls.push(`approveRecap:${ref}:${choice}${options?.post ? ":post" : ""}`);
      const r = await find(ref);
      if (!r.approval?.canApprove) throw new ApiError("not-allowed");
      if (choice !== "text" && !choices.canAudio) throw new ApiError("not-in-plan");
      if (choice !== "text" && choices.audioUnavailable) throw new ApiError("audio-unavailable");
      r.approval = { approved: true, choice, approvedAt: 1791000100, canApprove: false };
      // As the API does: an audio choice queues the audio, which the page then waits for.
      if (choice !== "text")
        r.audio = { state: "queued", error: "", current: false, canAskAgain: false };
      if (options?.post) {
        // As the API does in the same call: handed to the bot, so nothing is left to edit or post.
        const now = Math.floor(Date.now() / 1000);
        Object.assign(r, {
          status: "posting",
          canEdit: false,
          canPost: false,
          canDrop: false,
          canTryAgain: false,
          postingSince: now,
          postingStep: "check",
          serverNow: now,
        });
      }
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
          // Files named with "unknown" or "nonames" stand in for the reader's two new refusals (#2302).
          const refusal = f.name.includes("unknown") ? "unknown_format" : f.name.includes("nonames") ? "no_speaker_names" : null;
          if (refusal)
            return { name: f.name, format: null, error: refusal, suggestedDate: null, lineCount: 0, leftOut: 0, speakers: [] };
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
        ...recapFacts(ref),
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
        ...recapFacts(ref),
      };
    },
    async importProgress(ref, importId) {
      calls.push(`importProgress:${ref}:${importId}`);
      const seen = calls.filter((c) => c.startsWith("importProgress:")).length;
      const state = seen < 2 ? ("working" as const) : ("done" as const);
      const now = Math.floor(Date.now() / 1000);
      return {
        // The whole import's progress, the shared shape (#2564).
        import: {
          stage: state === "done" ? "done" : "cleaning",
          done: state === "done" ? 1 : 0,
          total: 1,
          unit: "sessions",
          startedAt: now - 30,
          updatedAt: now - 2,
          serverNow: now,
          state: state === "done" ? ("done" as const) : ("running" as const),
          reason: null,
        },
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
    // The relationship web (#2324): rings out from a name, the way the real API counts hops.
    async webGraph(ref, query: WebQuery): Promise<WebGraph> {
      calls.push(`webGraph:${ref}:center=${query.center ?? ""}:hops=${query.hops}${query.view ? `:view=${query.view}` : ""}`);
      await reachable();
      signedIn();
      return webGraphOf(ref, query);
    },
    // The overview (#2632): every visible name, the ones with no connection yet, and the groups.
    async webOverview(ref, query) {
      calls.push(`webOverview:${ref}${query.view ? `:view=${query.view}` : ""}`);
      await reachable();
      signedIn();
      const view = webView(ref);
      if (view === "none") throw new ApiError("not-found");
      const dm = view === "dm";
      const visibility = webVisibility(ref);
      if (!dm && query.view === "full" && visibility === "private") throw new ApiError("peek-off");
      const all = dm ? query.view !== "players" : query.view === "full";
      const state = web(ref);
      const seen = state.nodes.filter((n) => all || (!n.secret && n.revealed !== false));
      const ids = new Set(seen.map((n) => n.id));
      const usable = state.links.filter(
        (l) => ids.has(l.a) && ids.has(l.b) && (all || (!l.secret && l.revealed !== false)),
      );
      const degree = new Map<string, number>(seen.map((n) => [n.id, 0]));
      for (const l of usable) {
        degree.set(l.a, (degree.get(l.a) ?? 0) + 1);
        degree.set(l.b, (degree.get(l.b) ?? 0) + 1);
      }
      const nodeOf = (n: WebNode): WebNode =>
        all ? { ...n, revealed: n.revealed !== false } : { id: n.id, name: n.name, kind: n.kind };
      const left = new Set(seen.filter((n) => (degree.get(n.id) ?? 0) > 0).map((n) => n.id));
      const groups: WebGroup[] = [];
      while (left.size > 0) {
        const start = [...left][0]!;
        const part = new Set([start]);
        const queue = [start];
        for (let i = 0; i < queue.length; i += 1) {
          for (const l of usable) {
            const there = l.a === queue[i] ? l.b : l.b === queue[i] ? l.a : null;
            if (there !== null && !part.has(there)) {
              part.add(there);
              queue.push(there);
            }
          }
        }
        part.forEach((id) => left.delete(id));
        const centre = [...part].sort((x, y) => (degree.get(y) ?? 0) - (degree.get(x) ?? 0))[0]!;
        groups.push({ center: centre, centerName: state.nodes.find((n) => n.id === centre)!.name, size: part.size });
      }
      groups.sort((x, y) => y.size - x.size);
      const tray = seen.filter((n) => (degree.get(n.id) ?? 0) === 0).map(nodeOf);
      return {
        counts: { names: seen.length, connections: usable.length, connectedNames: seen.length - tray.length, notConnected: tray.length },
        tray,
        groups,
        truncated: false,
        canEdit: dm && all,
        view: all ? "full" : "players",
      };
    },
    async webSize(ref, query: WebQuery) {
      calls.push(`webSize:${ref}:center=${query.center ?? ""}:hops=${query.hops}`);
      await reachable();
      signedIn();
      return { names: webGraphOf(ref, query).nodes.length };
    },
    // A player who agreed to the warning peeks (#2405); the DM would be told once on the DM screen.
    // What waits for the DM (#2635, #2652): a fact, a condition (no "keep it secret"), connections from two
    // uploads (one waiting on a name) and a "Which Mara?" question. Answers remove what they answer.
    async webWaiting(ref) {
      calls.push(`webWaiting:${ref}`);
      await reachable();
      signedIn();
      if (webView(ref) !== "dm") throw new ApiError("not-found");
      const waiting = (waitingState[ref] ??= pretendWaiting());
      return structuredClone({
        ...waiting,
        counts: {
          claims: waiting.claims.length,
          backstory: waiting.backstory.length,
          ready: waiting.backstory.filter((b) => b.ready).length,
          which: waiting.which.length,
          all: waiting.claims.length + waiting.backstory.length + waiting.which.length,
        },
      });
    },
    async answerWaiting(ref, answer) {
      calls.push(
        `answerWaiting:${ref}:${answer.kind}:${answer.action}:${"ids" in answer ? answer.ids.join(",") : answer.id}${"entity" in answer ? `:${answer.entity}` : ""}${"secret" in answer && answer.secret ? ":secret" : ""}`,
      );
      await reachable();
      signedIn();
      if (webView(ref) !== "dm") throw new ApiError("not-found");
      const waiting = (waitingState[ref] ??= pretendWaiting());
      const ids = "ids" in answer ? answer.ids : [answer.id];
      if (answer.kind === "which" && answer.action === "pick") {
        const q = waiting.which.find((x) => x.id === answer.id);
        if (!q?.candidates.some((c) => c.id === answer.entity)) throw new ApiError("gone");
      }
      const before = waiting.claims.length + waiting.backstory.length + waiting.which.length;
      waiting.claims = waiting.claims.filter((c) => !ids.includes(c.id));
      waiting.backstory = waiting.backstory.filter((b) => !(ids.includes(b.id) && (b.ready || answer.action === "skip")));
      waiting.which = waiting.which.filter((q) => !ids.includes(q.id));
      if (before === waiting.claims.length + waiting.backstory.length + waiting.which.length) throw new ApiError("gone");
      return answer.action === "keep_all" ? { kept: ids.length } : {};
    },
    async webPeek(ref) {
      calls.push(`webPeek:${ref}`);
      await reachable();
      signedIn();
      if (webView(ref) !== "manager" || webVisibility(ref) === "private") throw new ApiError("peek-off");
    },
    // A DM shows a name or a connection to the players (#2405).
    async webReveal(ref, target: WebRevealTarget) {
      calls.push(`webReveal:${ref}:${target.kind}:${target.id}`);
      await reachable();
      signedIn();
      if (webView(ref) !== "dm") throw new ApiError("not-allowed");
      const state = web(ref);
      const found = target.kind === "name" ? state.nodes.find((n) => n.id === target.id) : state.links.find((l) => l.id === target.id);
      if (!found) throw new ApiError("not-found");
      found.revealed = true;
    },
    async changeWebLink(ref, change: WebLinkChange) {
      calls.push(`changeWebLink:${ref}:${JSON.stringify(change)}`);
      await reachable();
      signedIn();
      if (webView(ref) !== "dm") throw new ApiError(webView(ref) === "none" ? "not-found" : "not-allowed");
      const state = web(ref);
      const edgeOf = (a: string, b: string): WebEdge | { removed: true } => {
        const found = edgesOf(state.links.filter((l) => (l.a === a && l.b === b) || (l.a === b && l.b === a)), true, true, true);
        return found[0] ?? { removed: true };
      };
      if (change.action === "add") {
        if (change.words.trim() === "") throw new ApiError("bad-name");
        if (!state.nodes.some((n) => n.id === change.a) || !state.nodes.some((n) => n.id === change.b)) throw new ApiError("not-found");
        state.links.push({ id: `l${state.nextLink++}`, a: change.a, b: change.b, words: change.words.trim(), secret: change.secret, direction: "ab" });
        return edgeOf(change.a, change.b);
      }
      const link = state.links.find((l) => l.id === change.id);
      if (!link) throw new ApiError("not-found");
      if (change.action === "words") {
        if (change.words.trim() === "") throw new ApiError("bad-name");
        link.words = change.words.trim();
      } else if (change.action === "secret") {
        link.secret = change.secret;
      } else if (change.action === "pin") {
        link.pinned = change.pinned;
      } else {
        if (change.confirm !== true) throw new ApiError("needs-confirm");
        state.links.splice(state.links.indexOf(link), 1);
      }
      return edgeOf(link.a, link.b);
    },
    // The pretend campaign page (#2416): a ref ending `.cp-none` is one nobody may see, `.cp-player` one
    // the person plays in without running it, `.cp-co` one they help run.
    async campaignPage(ref): Promise<CampaignPage> {
      calls.push(`campaignPage:${ref}`);
      await reachable();
      signedIn();
      if (/\.cp-none$/.test(ref)) throw new ApiError("not-found");
      const mine = campaigns.find((c) => c.ref === ref);
      const role = /\.cp-player$/.test(ref) ? "player" : /\.cp-co$/.test(ref) ? "co-dm" : "owner";
      return {
        ref,
        name: mine?.name ?? "The Brynwater Crossing",
        serverName: mine?.serverName ?? "Thursday Table",
        role,
        importsOn: mine?.importsOn ?? false,
        // Only a DM gets the campaign's current choices (#2461), as the real API does.
        ...(role === "player"
          ? {}
          : {
              settings: { visibility: "peek", level: "normal", keepDays: 90, paused: false, gameMasters: 2 } as const,
            }),
      };
    },
    // The pretend settings queue (#2484): the bot answers on the next look, as the real one does within
    // seconds. A ref ending `.cs-offline` is never answered (the bot is away), `.cs-refused` is
    // refused by Discord's rules, `.cs-failed` goes wrong; a shorter keep-time needs `confirm`.
    async setCampaignSetting(ref, choice): Promise<string> {
      calls.push(`setCampaignSetting:${ref}:${choice.setting}:${choice.value}${choice.confirm ? ":confirm" : ""}`);
      await reachable();
      signedIn();
      if (/\.cp-none$/.test(ref)) throw new ApiError("not-found");
      if (/\.cp-player$/.test(ref)) throw new ApiError("not-allowed");
      const id = `a${settingAnswers.size + 1}`;
      settingAnswers.set(id, { ref, ...choice });
      return id;
    },
    async campaignSettingAnswer(ref, answer): Promise<CampaignSettingAnswer> {
      calls.push(`campaignSettingAnswer:${ref}:${answer}`);
      await reachable();
      signedIn();
      const asked = settingAnswers.get(answer);
      if (!asked || asked.ref !== ref) throw new ApiError("not-found");
      const base = { setting: asked.setting, value: asked.value, note: "", error: "" };
      if (/\.cs-offline$/.test(ref)) return { ...base, state: "waiting" };
      // `.cs-late`: the bot answers on the 40th look, long after the page gave up waiting live.
      if (/\.cs-late$/.test(ref) && (lateLooks.set(answer, (lateLooks.get(answer) ?? 0) + 1).get(answer) ?? 0) < 40)
        return { ...base, state: "waiting" };
      if (/\.cs-refused$/.test(ref)) return { ...base, state: "failed", error: "refused", note: "That limit is **not** allowed." };
      if (/\.cs-failed$/.test(ref)) return { ...base, state: "failed", error: "failed" };
      if (asked.setting === "retention" && asked.value === "30" && asked.confirm !== true)
        return { ...base, state: "failed", error: "needs_confirm" };
      const note =
        asked.setting === "retention" ? `Saved. Sessions older than ${asked.value} days are deleted every night.` : "";
      return { ...base, state: "done", note };
    },
    // The pretend NPC pages (#2266): entry "gone" is not found; a campaign ending `.npc-outside` is
    // one the person is not in; `.npc-dm` shows a DM's own note.
    async npcPage(ref, entry): Promise<NpcPage> {
      calls.push(`npcPage:${ref}:${entry}`);
      await reachable();
      signedIn();
      if (/\.npc-outside$/.test(ref)) throw new ApiError("not-in-server");
      if (entry === "gone") throw new ApiError("not-found");
      const isDm = /\.npc-dm$/.test(ref);
      return {
        id: entry,
        name: "Mira Voss",
        kind: "npc",
        isGroup: false,
        alsoCalled: ["Voss", "the Ferrywoman"],
        groups: ["The Brynwater Ferrymen"],
        groupsMore: 0,
        ties: ["Captain Harl Brask (works for)"],
        tiesMore: 2,
        recaps: [{ ref: `${ref}.1791000000`, title: "The night on the ferry", day: "12 Oct" }],
        myNote: npcNotes.get(entry) ?? "",
        isDm,
        dmNote: isDm ? "Knows who burned the mill." : null,
      };
    },
    async saveNpcNote(ref, entry, body): Promise<void> {
      calls.push(`saveNpcNote:${ref}:${entry}:${body}`);
      await reachable();
      signedIn();
      if (body.length > 5000) throw new ApiError("bad-text");
      if (body === "") npcNotes.delete(entry);
      else npcNotes.set(entry, body);
    },
    async myNpcNotes(ref): Promise<MyNpcNote[]> {
      calls.push(`myNpcNotes:${ref}`);
      await reachable();
      signedIn();
      return [...npcNotes].map(([id, body]) => ({
        id,
        name: id === "old" ? "Old Tam" : "Mira Voss",
        body,
        updatedAt: 1791000000,
        listed: id !== "old",
      }));
    },
    async deleteMyNpcNotes(ref): Promise<number> {
      calls.push(`deleteMyNpcNotes:${ref}`);
      await reachable();
      signedIn();
      const n = npcNotes.size;
      npcNotes.clear();
      return n;
    },
    async allNames(ref, query: NamesQuery): Promise<NamesPage> {
      calls.push(`allNames:${ref}:q=${query.q}:kind=${query.kind}:sort=${query.sort}:page=${query.page}`);
      await reachable();
      signedIn();
      const view = namesView(ref);
      if (view === "none") throw new ApiError("not-found");
      const visible = memory.filter((n) => view === "dm" || !n.secret);
      const kinds: NameKind[] = NAME_KINDS.map((k) => ({ ...k, count: visible.filter((n) => n.kind === k.key).length })).filter((k) => k.count > 0);
      const needle = query.q.trim().toLowerCase();
      const found = visible
        .filter((n) => query.kind === "" || n.kind === query.kind)
        .filter((n) => needle === "" || [n.name, ...n.others].some((x) => x.toLowerCase().includes(needle)));
      const sorted = [...found].sort((a, b) => (query.sort === "name" ? a.name.localeCompare(b.name) : Number(b.id.slice(1)) - Number(a.id.slice(1))));
      const pageSize = 20;
      const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
      if (query.page < 1 || query.page > pages) throw new ApiError("bad-page");
      return {
        campaign: "The Brynwater Crossing",
        canEdit: view === "dm",
        // A DM's rows carry the main name's id and how it is said (#2317); a manager's do not.
        names: sorted
          .slice((query.page - 1) * pageSize, query.page * pageSize)
          .map((n) => ({ ...n, others: [...n.others], ...(view === "dm" ? { aliasId: `al-${n.id}`, saidAs: memorySaid.get(n.id) ?? null } : {}) })),
        kinds,
        total: sorted.length,
        page: query.page,
        pages,
        pageSize,
      };
    },
    async changeName(ref, id, change: NameChange): Promise<NameChanged> {
      calls.push(`changeName:${ref}:${id}:${JSON.stringify(change)}`);
      await reachable();
      signedIn();
      if (namesView(ref) !== "dm") throw new ApiError("not-allowed");
      const row = memory.find((n) => n.id === id);
      if (!row) throw new ApiError("not-found");
      const taken = (text: string): boolean =>
        memory.some((n) => n.id !== id && [n.name, ...n.others].some((x) => x.toLowerCase() === text.trim().toLowerCase()));
      switch (change.action) {
        case "spell": {
          const next = change.name.trim();
          if (next === "") throw new ApiError("bad-name");
          if (next.length > NAME_LIMIT) throw new ApiError("ticket-too-long");
          if (taken(next)) throw new ApiError("name-exists");
          row.name = next;
          break;
        }
        case "kind":
          if (!NAME_KINDS.some((k) => k.key === change.kind)) throw new ApiError("bad-kind");
          row.kind = change.kind;
          if (change.kind !== "npc") row.npcId = null;
          break;
        case "add-other": {
          const other = change.other.trim();
          if (other === "") throw new ApiError("bad-name");
          if (taken(other) || row.others.includes(other)) throw new ApiError("name-exists");
          row.others.push(other);
          break;
        }
        case "remove-other":
          row.others = row.others.filter((o) => o !== change.other);
          break;
        case "hide":
          row.hidden = change.hidden;
          break;
        case "secret":
          break; // a single other name's switch: the pretend memory keeps none
        case "merge": {
          const into = memory.find((n) => n.id === change.into);
          if (!into || into.id === id) throw new ApiError("not-found");
          into.others.push(row.name, ...row.others.filter((o) => !into.others.includes(o)));
          memory.splice(memory.indexOf(row), 1);
          return { ...into, others: [...into.others] };
        }
        case "merge-many": {
          // The queue action of #2427: every other name goes into this one, in one change.
          const others = [...new Set(change.others)].map((other) => memory.find((n) => n.id === other));
          if (others.length === 0 || others.some((o) => !o)) throw new ApiError("not-found");
          if (change.others.includes(id)) throw new ApiError("bad-target");
          for (const other of others as NameRow[]) {
            row.others.push(other.name, ...other.others.filter((o) => !row.others.includes(o)));
            memory.splice(memory.indexOf(other), 1);
          }
          return { ...row, others: [...row.others] };
        }
        case "remove":
          memory.splice(memory.indexOf(row), 1);
          return { removed: true };
      }
      return { ...row, others: [...row.others] };
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
      return mockTranscript(number, version, putBackNow);
    },
    async transcriptStretch(campaignRef, number, skip) {
      calls.push(`transcriptStretch:${campaignRef}:${number}:${skip.speaker}:${skip.fromMs}:${skip.toMs}`);
      await reachable();
      signedIn();
      if (number !== 2 || skip.speaker !== MOCK_SKIP.speaker) throw new ApiError("not-found");
      // (a line with markup in it: it must be shown as text, never as HTML)
      return [
        { when: "0:01:02", who: "Mia", text: "did you see the match" },
        { when: "0:01:05", who: "Ash", text: "<b>no</b> I missed it" },
        { when: "0:01:09", who: "Mia", text: "unreal" },
      ];
    },
    // (a campaign address ending `.player` is a player's: the API refuses them both, as it refuses anyone who isn't a DM)
    async transcriptPutBack(campaignRef, number, skip) {
      calls.push(`transcriptPutBack:${campaignRef}:${number}:${skip.speaker}:${skip.fromMs}:${skip.toMs}`);
      await reachable();
      signedIn();
      if (campaignRef.endsWith(".player")) throw new ApiError("not-allowed");
      const was = putBackNow;
      putBackNow = true;
      return was ? 0 : 3;
    },
    async transcriptLeaveOut(campaignRef, number, skip) {
      calls.push(`transcriptLeaveOut:${campaignRef}:${number}:${skip.speaker}:${skip.fromMs}:${skip.toMs}`);
      await reachable();
      signedIn();
      if (campaignRef.endsWith(".player")) throw new ApiError("not-allowed");
      const was = putBackNow;
      putBackNow = false;
      return was ? 3 : 0;
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
    // The pretend Story timeline (#2462): a campaign ending `.tl-empty` has no sessions, `.tl-player`
    // is a player's view (posted recaps only, no address).
    async timeline(campaignRef): Promise<Timeline> {
      calls.push(`timeline:${campaignRef}`);
      await reachable();
      signedIn();
      if (/\.tl-none$/.test(campaignRef)) throw new ApiError("not-found");
      const player = /\.tl-player$/.test(campaignRef);
      const base = {
        ref: campaignRef,
        name: "The Brynwater Crossing",
      };
      if (/\.tl-empty$/.test(campaignRef)) return { ...base, sessions: [] };
      return {
        ...base,
        sessions: [
          {
            number: 1,
            startedAt: 1_790_000_000,
            endedAt: 1_790_010_800,
            recap: { title: "The river crossing", posted: true, ref: player ? null : `${campaignRef}.1790000000` },
            revealed: ["Ferryman Odo", "Hollow Mill"],
          },
          {
            number: 2,
            startedAt: 1_790_600_000,
            endedAt: 1_790_607_200,
            recap: player ? null : { title: "The abbey at dusk", posted: false, ref: `${campaignRef}.1790600000` },
            revealed: [],
          },
          { number: 3, startedAt: 1_791_200_000, endedAt: null, recap: null, revealed: [] },
        ],
      };
    },
    async sharedRules() {
      calls.push("sharedRules");
      await reachable();
      signedIn();
      return [
        {
          ref: "200000000000000001.cmp1",
          name: "The Brynwater Crossing",
          serverName: "Thursday Table",
          publishes: [
            { kind: "house-rules", name: "Brynwater table rules", version: 3, shared: true, hasCode: true },
            { kind: "homebrew", name: "Marsh creatures", version: 1, shared: false, hasCode: false },
          ],
          follows: [
            { kind: "homebrew", name: "Frostmaiden pack", publisher: "The Long Winter", version: 4, updating: true },
            { kind: "house-rules", name: "Old Guard rules", publisher: "Grey Keep", version: 2, updating: false },
          ],
        },
      ];
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
    // "How to say it" (#2317): a made-up respelling, the first half in capitals (suggest) or the second
    // (from a recording), so the two can be told apart in a demo.
    async suggestSaidAs(campaignRef, nameId) {
      calls.push(`suggestSaidAs:${campaignRef}:${nameId}`);
      await reachable();
      signedIn();
      const found = sayable(nameId);
      if (!found) throw new ApiError("not-found");
      const half = Math.ceil(found.text.length / 2);
      return `${found.text.slice(0, half).toUpperCase()}-${found.text.slice(half).toLowerCase()}`;
    },
    async respellFromVoice(campaignRef, nameId, clip) {
      calls.push(`respellFromVoice:${campaignRef}:${nameId}:${clip.type}`);
      await reachable();
      signedIn();
      const found = sayable(nameId);
      if (!found) throw new ApiError("not-found");
      if (clip.size === 0) throw new ApiError("bad-clip");
      const half = Math.ceil(found.text.length / 2);
      return `${found.text.slice(0, half).toLowerCase()}-${found.text.slice(half).toUpperCase()}`;
    },
    async saveSaidAs(campaignRef, nameId, saidAs) {
      calls.push(`saveSaidAs:${campaignRef}:${nameId}:${saidAs ?? ""}`);
      await reachable();
      signedIn();
      const found = sayable(nameId);
      if (!found) throw new ApiError("not-found");
      found.set(saidAs === null || saidAs.trim() === "" ? null : saidAs.trim());
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
    // "Tell DMbot what to fix" (#2114). The note is sorted into the same points every time; the
    // first look says "working", the next says "done", as the bot's queue would.
    async sendFix(ref, text) {
      calls.push(`sendFix:${ref}`);
      const r = await find(ref);
      if (!r.canEdit) throw new ApiError("not-editable");
      if (text.trim() === "") throw new ApiError("bad-text");
      if (text.length > 1000) throw new ApiError("too-big");
      fixLooks.set("r1", 0);
      return { requestId: "r1" };
    },
    async fixResult(ref, requestId) {
      calls.push(`fixResult:${ref}:${requestId}`);
      const looks = (fixLooks.get(requestId) ?? 0) + 1;
      fixLooks.set(requestId, looks);
      if (looks < 2) return { state: "working" as const };
      return {
        state: "done" as const,
        points: {
          corrections: [
            { id: "c1", text: "Fix the innkeeper's name" },
            { id: "c2", text: "Shorten the second part" },
          ],
          style: [{ id: "s1", text: "A warmer voice" }],
          replies: ["I can't add story that didn't happen. That's your call."],
        },
      };
    },
    // "Speak instead" (#2113 part 2). The clip is never kept: only its size and type are looked at.
    // A clip of 7 bytes is a DM who has not agreed to be recorded, 13 bytes has no words in it.
    async sendVoice(ref, clip) {
      calls.push(`sendVoice:${ref}:${clip.type}:${clip.size}`);
      const r = await find(ref);
      if (!r.canEdit) throw new ApiError("not-editable");
      if (clip.type !== "audio/webm" && clip.type !== "audio/ogg") throw new ApiError("bad-type");
      if (clip.size > 2 * 1024 * 1024) throw new ApiError("too-big");
      voiceLooks.set("v1", { looks: 0, size: clip.size });
      return { requestId: "v1" };
    },
    async voiceResult(ref, requestId) {
      calls.push(`voiceResult:${ref}:${requestId}`);
      const seen = voiceLooks.get(requestId) ?? { looks: 0, size: 0 };
      seen.looks += 1;
      voiceLooks.set(requestId, seen);
      if (seen.looks < 2) return { state: "working" as const };
      if (seen.size === 7) return { state: "failed" as const, error: "no_consent" };
      if (seen.size === 13) return { state: "failed" as const, error: "no_words" };
      return { state: "done" as const, text: "Change the innkeeper's name to Brannoch." };
    },
    async rewriteWith(ref, requestId, corrections, style) {
      calls.push(`rewriteWith:${ref}:${requestId}:${corrections.join("+")}|${style.join("+")}`);
      const r = await find(ref);
      if (r.triesLeft <= 0) throw new ApiError("no-tries");
      const known = new Set(["c1", "c2", "s1"]);
      if ([...corrections, ...style].some((id) => !known.has(id))) throw new ApiError("bad-choice");
      r.triesLeft -= 1;
      r.working = true;
      r.canEdit = false;
      r.canPost = false;
      r.canTryAgain = false;
      r.approval = { approved: false, choice: "", approvedAt: null, canApprove: false };
      return { recap: { ...r }, triesLeft: r.triesLeft, needsApproval: true };
    },
    // Sharing a recap by a link (#2119, #2419). Only a posted or approved recap can have one.
    async shareRecap(ref, action) {
      calls.push(`shareRecap:${ref}:${action}`);
      const r = await find(ref);
      const unposted = r.status === "draft" && r.approval?.approved === true;
      if (r.status === "dropped" || (r.status === "draft" && !unposted))
        throw new ApiError("not-posted");
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
      return {
        title: r.title,
        text: r.text,
        postedAt: r.sessionAt,
        sessionAt: r.sessionAt,
        sessionNumber: r.sessionNumber ?? null,
        sessionSeconds: r.sessionSeconds ?? null,
        audio: {},
      };
    },
    async decideRecap(ref, action) {
      calls.push(`${action}Recap:${ref}`);
      const r = await find(ref);
      if (action === "again" && !r.canTryAgain)
        throw new ApiError("not-allowed");
      if (action === "post")
        {
          const now = Math.floor(Date.now() / 1000);
          Object.assign(r, {
            status: "posting",
            canEdit: false,
            canPost: false,
            postingSince: now,
            postingStep: "check",
            serverNow: now,
          });
        }
      if (action === "drop")
        Object.assign(r, { status: "dropped", canEdit: false, canPost: false });
      if (action === "again") r.triesLeft -= 1;
      if (r.status !== "draft")
        Object.assign(r, { canTryAgain: false, canDrop: false });
      else r.canTryAgain = r.triesLeft > 0;
      if (action === "again") {
        Object.assign(r, { working: true, state: "working", canTryAgain: false, canDrop: false });
        writing.set(ref, 1);
      }
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
    // The owner's side of a hand-over by email (#2534): one open offer per campaign, and an answer that
    // never says anything about the address. `bad` is not an address; `many@example.com` is over the limit.
    async offerHandover(ref, email) {
      calls.push(`offerHandover:${ref}:${email}`);
      await reachable();
      signedIn();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new ApiError("bad-email");
      if (email.startsWith("many@")) throw new ApiError("too-soon");
      if (!me?.campaigns.some((x) => x.ref === ref)) throw new ApiError("not-found");
      // (copied, never changed in place: the pretend campaigns are shared by every test)
      const open = { sentAt: "2026-10-23T12:00:00Z", expiresAt: "2026-10-30T12:00:00Z" };
      me = { ...me, campaigns: me.campaigns.map((x) => (x.ref === ref ? { ...x, handover: open } : x)) };
    },
    async deleteCampaign(ref, name) {
      calls.push(`deleteCampaign:${ref}:${name}`);
      await reachable();
      signedIn();
      const found = me?.campaigns.find((x) => x.ref === ref);
      if (!found) throw new ApiError("not-found");
      if (found.role !== "owner") throw new ApiError("not-allowed");
      if (/running/i.test(found.name)) throw new ApiError("session-running");
      if (name.split(/\s+/).join(" ").toLowerCase() !== found.name.split(/\s+/).join(" ").toLowerCase()) throw new ApiError("name-mismatch");
      // (copied, never changed in place: the pretend campaigns are shared by every test)
      me = { ...me!, campaigns: me!.campaigns.filter((x) => x.ref !== ref) };
      return { channelsLeft: found.serverReachable === false };
    },
    async campaignBackup(ref) {
      calls.push(`campaignBackup:${ref}`);
      await reachable();
      signedIn();
      if (me?.plan?.id === "try-it") throw new ApiError("no-backup");
      return new Blob([JSON.stringify({ campaign: ref })], { type: "application/json" });
    },
    async takeBackHandover(ref) {
      calls.push(`takeBackHandover:${ref}`);
      await reachable();
      signedIn();
      if (!me?.campaigns.some((x) => x.ref === ref)) throw new ApiError("not-found");
      me = { ...me, campaigns: me.campaigns.map((x) => (x.ref === ref ? { ...x, handover: null } : x)) };
    },
    // The hand-over accept page (#2534). Tokens: `open` (can accept), `no-plan`, `try-it`, `own`, `late`
    // (ended); `gone-after` looks open and ends before accepting.
    async handoverLook(token) {
      calls.push(`handoverLook:${token}`);
      await reachable();
      signedIn();
      if (token === "late" || !/^[\w-]{1,200}$/.test(token)) return { status: "ended" };
      const why = token === "no-plan" ? "no_plan" : token === "try-it" ? "try_it" : token === "own" ? "owner" : null;
      return {
        status: "open",
        campaign: { name: "The Silver Hall" },
        from: { name: "Mira" },
        server: { id: "s1", name: "Friday Night Dice" },
        canAccept: why === null,
        why,
        servers: [
          { id: "s1", name: "Friday Night Dice", current: true, botIn: true },
          { id: "s2", name: "Sunday Table", current: false, botIn: true },
          { id: "s3", name: "New Server", current: false, botIn: false },
        ],
      };
    },
    async handoverAccept(token, serverId) {
      calls.push(`handoverAccept:${token}:${serverId}`);
      await reachable();
      signedIn();
      if (token === "gone-after") throw new ApiError("ended");
      if (serverId === "s3") throw new ApiError("bad-server");
      return { campaignRef: "s1.cmp1" };
    },
    async handoverDecline(token) {
      calls.push(`handoverDecline:${token}`);
      await reachable();
      signedIn();
      if (token === "gone-after") throw new ApiError("ended");
    },
    addDiscordUrl: () => "#demo-add-discord",
    async removeDiscordAccount(key) {
      calls.push(`removeDiscordAccount:${key}`);
      await reachable();
      signedIn();
      const list = me?.discordAccounts ?? [];
      const found = list.find((a) => a.key === key);
      if (!found) throw new ApiError("not-found");
      if (list.length <= 1) throw new ApiError("last-account");
      const left = list.filter((a) => a.key !== key);
      me = { ...me!, discordAccounts: left.map((a) => ({ ...a, canRemove: left.length > 1 })) };
    },
    async getMoveCode() {
      calls.push("move-code");
      signedIn();
    },
    // The email setting (#2349). A confirmation link is "sent"; nothing changes until it is opened.
    async myEmail() {
      calls.push("myEmail");
      await reachable();
      signedIn();
      return { ...myEmail };
    },
    // The pretend link's code is "good-code": it confirms the waiting change, anything else is a bad link.
    async confirmEmail(token) {
      calls.push("confirmEmail");
      await reachable();
      signedIn();
      if (token !== "good-code" || !myEmail.pending) throw new ApiError("bad-link");
      myEmail.address = myEmail.pending;
      myEmail.pending = null;
    },
    async cancelEmailChange() {
      calls.push("cancelEmailChange");
      await reachable();
      signedIn();
      myEmail.pending = null;
    },
    async changeEmail(address) {
      calls.push(`changeEmail:${address}`);
      await reachable();
      signedIn();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address.trim())) throw new ApiError("bad-email");
      if (myEmail.source === "payment") throw new ApiError("not-allowed");
      if (address.trim().toLowerCase() === "taken@example.com") throw new ApiError("in-use");
      myEmail.pending = address.trim();
      return { pending: myEmail.pending };
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
    async supportThread(at) {
      calls.push("support-thread");
      signedIn();
      const found = questions.find((q) => q.at === at);
      if (!found) throw new ApiError("not-found");
      // The mock has only the first message; the real API adds the helper's and the team's.
      return [{ who: "you", text: found.subject, at }];
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
    async renewPlan() {
      calls.push("renew");
      if (me?.plan?.state !== "ending") throw new ApiError("not-ending");
      const { endsOn: _ended, ...rest } = me.plan;
      me = { ...me, plan: { ...rest, state: "active" } };
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
/** Session 2's cleaned reading has one left-out stretch (#2568): its marker is the third line. */
const MOCK_SKIP = { line: 2, speaker: 1, fromMs: 62_000, toMs: 76_000, seconds: 14, lines: 3 };

function mockTranscript(number: number, version: TranscriptVersion, putBack = false) {
  const heard = version === "heard";
  const skipped = number === 2 && !heard;
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
      ...(skipped && !putBack ? ["[0:01:02] (Speaker 1) [14s of off-topic chat skipped]"] : []),
      ...(skipped && putBack ? ["[0:01:02] (Speaker 1): did you see the match"] : []),
    ],
    ...(skipped ? { skips: [{ ...MOCK_SKIP, putBack }], filter: { kept: 412, leftOut: putBack ? 6 : 9, putBack: putBack ? 3 : 0 } } : {}),
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
 * the old shape, with no stage, is covered in the tests. */
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

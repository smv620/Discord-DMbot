// The pretend graph clean-up data (#3762, under #3740): what the clean-up page is shown in a pretend campaign. Kept out of
// mock.ts and loaded on demand, the same way mock-ai-feed.ts is, because every page that carries the pretend API counts
// toward the page weight.
import type { CleanupEntry, CleanupGroup, CleanupLink, CleanupView } from "./api";

/** The 3 groups of the default pretend campaign, largest first. */
function defaultGroups(): CleanupGroup[] {
  const g1: CleanupGroup = {
    id: "g1",
    entries: [
      { id: "e1", name: "Okthandom", kind: "npc", status: "confirmed", scene: "Okthandom led the party over the pass at dusk." },
      { id: "e2", name: "unnamed - Okthandom's child", kind: "npc", unnamed: true, unnamedWhat: "child", unnamedAnchor: "Okthandom", status: "waiting" },
      { id: "e3", name: "The Tide Wardens", kind: "group", status: "confirmed", scene: null },
    ],
    links: [
      { id: "l1", a: "e1", b: "e2", words: "parent of", status: "confirmed" },
      { id: "l2", a: "e1", b: "e3", words: "leads", status: "confirmed" },
    ],
  };
  const g2: CleanupGroup = {
    id: "g2",
    entries: [
      { id: "e4", name: "Old Pell", kind: "npc", status: "confirmed", scene: "Pell sold the party rope in the harbour market." },
      { id: "e5", name: "Brewner", kind: "npc", status: "confirmed", scene: null },
    ],
    links: [{ id: "l3", a: "e4", b: "e5", words: "drinks with", status: "waiting", ended: true }],
  };
  const g3: CleanupGroup = {
    id: "g3",
    entries: [
      {
        id: "e6",
        name: "unnamed - the ferryman's cousin",
        kind: "npc",
        unnamed: true,
        unnamedWhat: "cousin",
        unnamedAnchor: "The Ferryman",
        status: "confirmed",
        scene: "The ferryman mentioned a cousin who sells maps.",
      },
    ],
    links: [],
  };
  return [g1, g2, g3];
}

/** 150 groups of 3 entries each; the page lists the first 100 groups (300 entries) of the 450. */
function bigGroups(): CleanupGroup[] {
  const groups: CleanupGroup[] = [];
  for (let i = 0; i < 150; i += 1) {
    const entries: CleanupEntry[] = [0, 1, 2].map((j) => ({ id: `b${i}e${j}`, name: `Person ${i}-${j}`, kind: "npc", status: "confirmed" }));
    const links: CleanupLink[] = [
      { id: `b${i}l0`, a: `b${i}e0`, b: `b${i}e1`, words: "knows", status: "confirmed" },
      { id: `b${i}l1`, a: `b${i}e1`, b: `b${i}e2`, words: "knows", status: "confirmed" },
    ];
    groups.push({ id: `b${i}`, entries, links });
  }
  return groups.slice(0, 100);
}

/** The clean-up view for a pretend campaign; `kind` is the last part of its address (`cleanup`, `cleanup-empty`, ...). */
export function pretendCleanup(kind: string): CleanupView {
  switch (kind) {
    case "cleanup-empty":
      return { groups: [], total: 0, shown: 0, noPlayerCharacter: false, review: { state: "idle" } };
    case "cleanup-nopc":
      return { groups: [], total: 0, shown: 0, noPlayerCharacter: true };
    case "cleanup-big":
      return { groups: bigGroups(), total: 450, shown: 300, noPlayerCharacter: false };
    case "cleanup-done":
      return { groups: defaultGroups(), total: 6, shown: 6, noPlayerCharacter: false, review: { state: "done", missed: 2 } };
    case "cleanup-off":
      return { groups: defaultGroups(), total: 6, shown: 6, noPlayerCharacter: false, review: { state: "off" } };
    default:
      return { groups: defaultGroups(), total: 6, shown: 6, noPlayerCharacter: false, review: { state: "waiting", minutesLeft: 12, fixes: 2 } };
  }
}

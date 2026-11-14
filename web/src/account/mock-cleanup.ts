// The pretend graph clean-up data (#3762, under #3740): what the clean-up page is shown in a pretend campaign. Kept out of
// mock.ts and loaded on demand, the same way mock-ai-feed.ts is, because every page that carries the pretend API counts
// toward the page weight.
import type { CleanupEntry, CleanupGroup, CleanupView, WebEdge } from "./api";

/** One edge between two entries of a group, with one connection in it (the map's own shape). */
function edge(id: string, a: string, b: string, words: string): WebEdge {
  return { a, b, label: words, more: 0, links: [{ id, words, secret: false, direction: "a-b" }] };
}

/** The 3 groups of the default pretend campaign, largest first. */
function defaultGroups(): CleanupGroup[] {
  const g1: CleanupGroup = {
    entries: [
      { id: "e1", name: "Okthandom", kind: "npc", waiting: false, scene: "Okthandom led the party over the pass at dusk." },
      { id: "e2", name: "unnamed - Okthandom's child", kind: "npc", waiting: true, unnamed: true, unnamedWhat: "child", unnamedAnchor: "Okthandom" },
      { id: "e3", name: "The Tide Wardens", kind: "group", waiting: false },
    ],
    links: [edge("l1", "e1", "e2", "parent of"), edge("l2", "e1", "e3", "leads")],
  };
  const g2: CleanupGroup = {
    entries: [
      { id: "e4", name: "Old Pell", kind: "npc", waiting: false, scene: "Pell sold the party rope in the harbour market." },
      { id: "e5", name: "Brewner", kind: "npc", waiting: false },
    ],
    links: [edge("l3", "e4", "e5", "drinks with")],
  };
  const g3: CleanupGroup = {
    entries: [
      {
        id: "e6",
        name: "unnamed - the ferryman's cousin",
        kind: "npc",
        waiting: false,
        unnamed: true,
        unnamedWhat: "cousin",
        unnamedAnchor: "The Ferryman",
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
    const entries: CleanupEntry[] = [0, 1, 2].map((j) => ({ id: `b${i}e${j}`, name: `Person ${i}-${j}`, kind: "npc", waiting: false }));
    const links: WebEdge[] = [edge(`b${i}l0`, `b${i}e0`, `b${i}e1`, "knows"), edge(`b${i}l1`, `b${i}e1`, `b${i}e2`, "knows")];
    groups.push({ entries, links });
  }
  return groups.slice(0, 100);
}

/** The clean-up view for a pretend campaign; `kind` is the last part of its address (`cleanup`, `cleanup-empty`, ...). */
export function pretendCleanup(kind: string): CleanupView {
  switch (kind) {
    case "cleanup-empty":
      return { groups: [], total: 0, truncated: false, noPlayerCharacter: false, review: { state: "idle" } };
    case "cleanup-nopc":
      return { groups: [], total: 0, truncated: false, noPlayerCharacter: true };
    case "cleanup-big":
      return { groups: bigGroups(), total: 450, truncated: true, noPlayerCharacter: false };
    case "cleanup-done":
      return { groups: defaultGroups(), total: 6, truncated: false, noPlayerCharacter: false, review: { state: "done", missed: 2 } };
    case "cleanup-off":
      return { groups: defaultGroups(), total: 6, truncated: false, noPlayerCharacter: false, review: { state: "off" } };
    default:
      return { groups: defaultGroups(), total: 6, truncated: false, noPlayerCharacter: false, review: { state: "waiting", minutesLeft: 12, fixes: 2 } };
  }
}

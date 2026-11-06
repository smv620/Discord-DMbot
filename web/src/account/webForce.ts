// The Story Graph's free layout (#2934): names push each other apart, lines pull their two ends
// together, and the names settle by themselves so a crowded spot opens up. Written in plain code so the page
// needs no graph library (the site's weight budget, #1947). Pure, with no clock and no random numbers: the
// same names and lines always settle in the same places, which keeps the tests exact and a redraw calm.
import { KIND_STYLES, kindStyle } from "./webKinds";
import { radialLayout } from "./webLayout";

/** "free": names settle anywhere. "groups": names gather by what each one is (player characters, NPCs, places,
 * items, groups, events...: the kinds the Names page uses), not by how it is connected (#2990). "quests": names
 * gather by the quest they are joined to (#2992). */
export type LayoutMode = "free" | "groups" | "quests";

/** More names than this keep their rings and are only movable by hand: pushing every pair apart is
 * the slow part, and a web this big is already past what a screen can show clearly. */
export const MAX_SIM_NAMES = 200;
/** The most steps a settle takes; it stops sooner when the names are calm. */
export const MAX_SIM_STEPS = 300;

export interface SimNode {
  id: string;
  kind: string;
  /** The group it gathers with in the grouped layout: what it is (its kind). */
  group: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Held where the person dropped it: the forces do not move it. */
  pinned: boolean;
}

/** Saved places for names the person moved, by name id. */
export type SavedPlaces = Record<string, [number, number]>;

const REPEL = 5200; // how hard two names push apart (falls off with the square of the distance)
const CLOSE = 96; // names closer than this also get a short extra shove, so circles never overlap
const REST = 150; // the length a line likes to be
const PULL = 0.012; // how hard a line pulls its ends toward REST
const HOME = 0.0016; // a faint pull to the middle so loose names do not drift away
const CENTRE_HOME = 0.03; // the middle name stays near the middle
const DAMPING = 0.78;
const TOP_SPEED = 14;
const CALM = 0.05; // the biggest move, in drawing units, under which the names count as still
const COOLING = 0.985;

/** The names, placed first on the rings (so the start is tidy and the same every time), then wherever a
 * saved place says. Names with a saved place start pinned there. */
export function startNodes(
  nodes: { id: string; name: string; kind: string }[],
  edges: { a: string; b: string }[],
  center: string,
  saved: SavedPlaces = {},
): SimNode[] {
  const rings = radialLayout(nodes, edges, center);
  return nodes.map((n) => {
    const at = rings.points.get(n.id);
    const kept = saved[n.id];
    return {
      id: n.id,
      kind: n.kind,
      group: n.kind,
      x: kept ? kept[0] : (at?.x ?? 0),
      y: kept ? kept[1] : (at?.y ?? 0),
      vx: 0,
      vy: 0,
      pinned: kept !== undefined,
    };
  });
}

/** The kinds of connection, in the order they are shown. */
export const LINK_KINDS = ["family", "ally", "group", "work", "other"];

/** The kind a line has: the kind its connections share, else "other" (a mixed line has no one colour). */
export function edgeKind(edge: { links: { kind?: string | undefined }[] }): string {
  // A kind this page does not know (a newer API) is "other", so it always has a colour, a chip and an area.
  const kinds = new Set(edge.links.map((l) => (l.kind !== undefined && LINK_KINDS.includes(l.kind) ? l.kind : "other")));
  return kinds.size === 1 ? [...kinds][0]! : "other";
}

/** The kinds of name in the order their areas and the legend give them: the people first, then the places and
 * things, each style's kinds together by key (#2992). */
export function orderedKinds(kinds: Iterable<string>): string[] {
  const rank = (k: string): number => KIND_STYLES.indexOf(kindStyle(k));
  return [...new Set(kinds)].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** Where each group gathers in the grouped layout: the groups spread evenly around the middle. */
export function kindAnchors(kinds: string[]): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const radius = 190 + 25 * kinds.length;
  kinds.forEach((kind, i) => {
    const theta = (i / Math.max(1, kinds.length)) * 2 * Math.PI - Math.PI / 2;
    out.set(kind, { x: Math.cos(theta) * radius, y: Math.sin(theta) * radius * 0.8 });
  });
  return out;
}

/** The index pairs of the lines, for the names in `nodes`; a line to a name not here is left out. */
export function linkPairs(nodes: { id: string }[], edges: { a: string; b: string }[]): [number, number][] {
  const at = new Map(nodes.map((n, i) => [n.id, i]));
  const out: [number, number][] = [];
  for (const { a, b } of edges) {
    const i = at.get(a);
    const j = at.get(b);
    if (i !== undefined && j !== undefined && i !== j) out.push([i, j]);
  }
  return out;
}

/** One step of the forces. `alpha` (1 down to 0) is how much the names may still move. Returns the
 * biggest move made, so the caller can stop when the names are calm. */
export function forceStep(
  sim: SimNode[],
  links: [number, number][],
  centreIndex: number,
  mode: LayoutMode,
  anchors: Map<string, { x: number; y: number }>,
  alpha: number,
): number {
  for (let i = 0; i < sim.length; i += 1) {
    for (let j = i + 1; j < sim.length; j += 1) {
      const a = sim[i]!;
      const b = sim[j]!;
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      // Two names on the very same spot get a fixed, different nudge so they can separate.
      if (d2 < 0.01) {
        dx = (j - i) % 2 === 0 ? 1 : -1;
        dy = 1;
        d2 = 2;
      }
      const d = Math.sqrt(d2);
      let f = REPEL / d2;
      if (d < CLOSE) f += (CLOSE - d) * 0.06;
      dx /= d;
      dy /= d;
      a.vx -= dx * f;
      a.vy -= dy * f;
      b.vx += dx * f;
      b.vy += dy * f;
    }
  }
  const rest = mode !== "free" ? 130 : REST;
  const pull = mode !== "free" ? PULL * 0.4 : PULL;
  for (const [i, j] of links) {
    const a = sim[i]!;
    const b = sim[j]!;
    let dx = b.x - a.x;
    let dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const f = (d - rest) * pull;
    dx /= d;
    dy /= d;
    a.vx += dx * f;
    a.vy += dy * f;
    b.vx -= dx * f;
    b.vy -= dy * f;
  }
  let moved = 0;
  sim.forEach((n, i) => {
    const home = i === centreIndex ? CENTRE_HOME : HOME;
    n.vx -= n.x * home;
    n.vy -= n.y * home;
    const anchor = mode !== "free" && i !== centreIndex ? anchors.get(n.group) : undefined;
    if (anchor) {
      n.vx += (anchor.x - n.x) * 0.022;
      n.vy += (anchor.y - n.y) * 0.022;
    }
    if (n.pinned) {
      n.vx = 0;
      n.vy = 0;
      return;
    }
    n.vx *= DAMPING;
    n.vy *= DAMPING;
    const speed = Math.hypot(n.vx, n.vy);
    if (speed > TOP_SPEED) {
      n.vx *= TOP_SPEED / speed;
      n.vy *= TOP_SPEED / speed;
    }
    n.x += n.vx * alpha;
    n.y += n.vy * alpha;
    moved = Math.max(moved, Math.hypot(n.vx * alpha, n.vy * alpha));
  });
  return moved;
}

/** Run the whole settle at once: for people who asked for less motion, and for the tests. Stops when the
 * names are calm or after MAX_SIM_STEPS. Returns the steps taken. */
export function settle(
  sim: SimNode[],
  links: [number, number][],
  centreIndex: number,
  mode: LayoutMode,
  anchors: Map<string, { x: number; y: number }>,
): number {
  if (sim.length > MAX_SIM_NAMES) return 0;
  let alpha = 1;
  for (let step = 1; step <= MAX_SIM_STEPS; step += 1) {
    const moved = forceStep(sim, links, centreIndex, mode, anchors, alpha);
    alpha *= COOLING;
    if (alpha < 0.3 && moved < CALM) return step;
  }
  return MAX_SIM_STEPS;
}

export { COOLING, CALM };

/** Half the width and height of a drawing that holds every name with room for its label. */
export function extentOf(sim: { x: number; y: number }[], floor = 220): number {
  let far = 0;
  for (const n of sim) far = Math.max(far, Math.abs(n.x), Math.abs(n.y));
  return Math.max(floor, far + 110);
}

/** The group a name gathers with in "Cluster by quest": a quest is its own group; any other name joins the quest it
 * has most connections with (the door sends them in that order), or "none" (#2992). */
export const NO_QUEST = "none";
export function questGroups(nodes: { id: string; kind: string; quests?: { id: string }[] | undefined }[]): Map<string, string> {
  return new Map(nodes.map((n) => [n.id, kindStyle(n.kind) === "quest" ? n.id : (n.quests?.[0]?.id ?? NO_QUEST)]));
}

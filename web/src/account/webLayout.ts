// The relationship web's layout (#2324): the chosen name in the middle and each ring one hop further out,
// worked out here in plain code so the page needs no graph library (the site's weight budget, #1947,
// has no room for one). Pure and deterministic: the same names and connections always land in the
// same places, so a redraw after a small change does not shuffle everything.

export interface Placed {
  id: string;
  x: number;
  y: number;
  /** Hops from the centre: 0 for the centre itself. */
  ring: number;
}

export interface Layout {
  points: Map<string, Placed>;
  /** The largest ring used. */
  rings: number;
  /** The radius of each ring, index = ring (0 for the centre). */
  radii: number[];
  /** Half the width and height of the drawing, from the middle, with room for the names. */
  extent: number;
}

const RING_GAP = 150;
const MIN_ARC = 96; // room along a ring for one name's circle and its label

/** Rings by hop distance from `center` over the edges; names that no edge reaches go on the outside. */
export function radialLayout(
  nodes: { id: string; name: string }[],
  edges: { a: string; b: string }[],
  center: string,
): Layout {
  const names = new Map(nodes.map((n) => [n.id, n.name]));
  const next = new Map<string, string[]>();
  for (const { a, b } of edges) {
    if (!names.has(a) || !names.has(b)) continue;
    (next.get(a) ?? next.set(a, []).get(a)!).push(b);
    (next.get(b) ?? next.set(b, []).get(b)!).push(a);
  }
  const ringOf = new Map<string, number>();
  if (names.has(center)) {
    ringOf.set(center, 0);
    const queue = [center];
    for (let i = 0; i < queue.length; i += 1) {
      const here = queue[i]!;
      for (const there of next.get(here) ?? []) {
        if (ringOf.has(there)) continue;
        ringOf.set(there, ringOf.get(here)! + 1);
        queue.push(there);
      }
    }
  }
  let rings = Math.max(0, ...ringOf.values());
  // Anything not reached (an API that sent a stray name) goes one ring past the last.
  const strays = nodes.filter((n) => !ringOf.has(n.id));
  if (strays.length > 0) {
    rings += 1;
    for (const n of strays) ringOf.set(n.id, rings);
  }
  const byRing: string[][] = Array.from({ length: rings + 1 }, () => []);
  for (const n of nodes) byRing[ringOf.get(n.id)!]!.push(n.id);

  const points = new Map<string, Placed>();
  const radii: number[] = [0];
  const angle = new Map<string, number>();
  for (let ring = 0; ring <= rings; ring += 1) {
    const ids = byRing[ring]!;
    if (ring === 0) {
      for (const id of ids) {
        points.set(id, { id, x: 0, y: 0, ring });
        angle.set(id, 0);
      }
      continue;
    }
    // Each name sits near the names it hangs from (their average direction), so lines cross less.
    const want = (id: string): number => {
      const parents = (next.get(id) ?? []).filter((p) => ringOf.get(p) === ring - 1);
      if (parents.length === 0) return 0;
      const x = parents.reduce((s, p) => s + Math.cos(angle.get(p) ?? 0), 0);
      const y = parents.reduce((s, p) => s + Math.sin(angle.get(p) ?? 0), 0);
      return Math.atan2(y, x);
    };
    const order = [...ids].sort((p, q) => {
      const d = want(p) - want(q);
      return d !== 0 ? d : (names.get(p) ?? "").localeCompare(names.get(q) ?? "") || p.localeCompare(q);
    });
    const radius = Math.max(ring * RING_GAP, (order.length * MIN_ARC) / (2 * Math.PI));
    radii.push(radius);
    order.forEach((id, i) => {
      const theta = (i / order.length) * 2 * Math.PI - Math.PI / 2;
      angle.set(id, theta);
      points.set(id, { id, x: Math.cos(theta) * radius, y: Math.sin(theta) * radius, ring });
    });
  }
  const extent = (radii[radii.length - 1] ?? 0) + 120;
  return { points, rings, radii, extent };
}

/** Whole numbers of hops from 1 to `max`; anything else becomes the nearest sensible value. */
export function clampHops(value: number, max: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(1, Math.round(value)), Math.max(1, max));
}

/** The map's position and size: shifted by (x, y) in drawing units, scaled by k. */
export interface View {
  x: number;
  y: number;
  k: number;
}

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 4;

/** Zoom by `factor` keeping the drawing point under `at` (in the drawing's own units, before the view
 * is applied) where it is on the screen, so zooming goes toward the finger or the pointer. */
export function zoomAbout(view: View, at: { x: number; y: number }, factor: number): View {
  const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
  const scale = k / view.k;
  return { k, x: at.x - (at.x - view.x) * scale, y: at.y - (at.y - view.y) * scale };
}

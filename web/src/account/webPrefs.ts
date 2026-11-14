// What a person chose on the Story Graph, kept in this browser only (#2934): which layout, and the
// places of the names they moved. Never sent to the server, so a DM's arrangement is never shared data and
// never reaches a player. Every read and write is guarded: private windows and blocked storage throw, and
// the map must work the same without it.
import type { LayoutMode } from "./webForce";
import type { SavedPlaces } from "./webForce";

const MODE_KEY = "dmbot:web-layout";
const AUTO_KEY = "dmbot:web-auto-update";
const placesKey = (campaign: string): string => `dmbot:web-places:${campaign}`;

export function readMode(): LayoutMode {
  try {
    const saved = window.localStorage.getItem(MODE_KEY);
    return saved === "groups" || saved === "quests" ? saved : "free";
  } catch {
    return "free";
  }
}

export function saveMode(mode: LayoutMode): void {
  try {
    window.localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* storage is blocked: the choice lasts until the page is closed */
  }
}

/** The saved places for one campaign, with anything that is not a pair of finite numbers dropped. */
export function readPlaces(campaign: string): SavedPlaces {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(placesKey(campaign)) ?? "{}");
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return {};
    const out: SavedPlaces = {};
    for (const [id, at] of Object.entries(raw)) {
      if (Array.isArray(at) && at.length === 2 && at.every((v) => typeof v === "number" && Number.isFinite(v))) {
        out[id] = [at[0] as number, at[1] as number];
      }
    }
    return out;
  } catch {
    return {};
  }
}

export function savePlaces(campaign: string, places: SavedPlaces): void {
  try {
    if (Object.keys(places).length === 0) window.localStorage.removeItem(placesKey(campaign));
    else window.localStorage.setItem(placesKey(campaign), JSON.stringify(places));
  } catch {
    /* storage is blocked: the names stay where they are until the page is closed */
  }
}

/** Whether the map reads itself again every few seconds. On unless the person froze it. */
export function readAuto(): boolean {
  try {
    return window.localStorage.getItem(AUTO_KEY) !== "off";
  } catch {
    return true;
  }
}

export function saveAuto(on: boolean): void {
  try {
    if (on) window.localStorage.removeItem(AUTO_KEY);
    else window.localStorage.setItem(AUTO_KEY, "off");
  } catch {
    /* storage is blocked: the choice lasts until the page is closed */
  }
}

/** How the viewer likes the map drawn (#3620): which kinds of name to show (none listed shows all), how far apart Spread out
 * puts them, how big names and their words are (1 is today's), and whether a line grows with its connections. */
export interface GraphLook {
  kinds: string[];
  spacing: number;
  nodeSize: number;
  textSize: number;
  lineWidth: boolean;
}
export const DEFAULT_LOOK: GraphLook = { kinds: [], spacing: 1, nodeSize: 1, textSize: 1, lineWidth: true };
/** The sliders' ranges: wide enough to matter, narrow enough that names stay readable and tappable at phone width. */
export const LOOK_RANGE = { spacing: [0.6, 1.8], nodeSize: [0.7, 1.5], textSize: [0.8, 1.6] } as const;
const LOOK_KEY = "dmbot:web-look";

const within = (v: unknown, [lo, hi]: readonly [number, number], fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;

/** The saved look, anything missing or out of range falling back to today's. Kept in this browser only, per viewer. */
export function readLook(): GraphLook {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(LOOK_KEY) ?? "{}");
    const o = typeof raw === "object" && raw !== null && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
    return {
      kinds: Array.isArray(o.kinds) ? o.kinds.filter((k): k is string => typeof k === "string").slice(0, 40) : [],
      spacing: within(o.spacing, LOOK_RANGE.spacing, 1),
      nodeSize: within(o.nodeSize, LOOK_RANGE.nodeSize, 1),
      textSize: within(o.textSize, LOOK_RANGE.textSize, 1),
      lineWidth: o.lineWidth !== false,
    };
  } catch {
    return { ...DEFAULT_LOOK };
  }
}

export function saveLook(look: GraphLook): void {
  try {
    if (JSON.stringify(look) === JSON.stringify(DEFAULT_LOOK)) window.localStorage.removeItem(LOOK_KEY);
    else window.localStorage.setItem(LOOK_KEY, JSON.stringify(look));
  } catch {
    /* storage is blocked: the choices last until the page is closed */
  }
}

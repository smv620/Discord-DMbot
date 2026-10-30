// What a person chose on the Story Graph, kept in this browser only (#2934): which layout, and the
// places of the names they moved. Never sent to the server, so a DM's arrangement is never shared data and
// never reaches a player. Every read and write is guarded: private windows and blocked storage throw, and
// the map must work the same without it.
import type { LayoutMode } from "./webForce";
import type { SavedPlaces } from "./webForce";

const MODE_KEY = "dmbot:web-layout";
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

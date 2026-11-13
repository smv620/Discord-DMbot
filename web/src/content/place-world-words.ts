// The words of a place's land, climate and ruler on the Names page (#4287): exactly Discord's (core/src/dmbot/ui/place_world.py), so
// the two stay level. DM only: what players see of these is #4311.
export const LAND = ["arctic", "coastal", "desert", "forest", "grassland", "hill", "mountain", "swamp", "underdark", "urban", "waterborne"] as const;
export const WARMTH = ["arctic", "cold", "temperate", "warm", "hot"] as const;
export const HOWS = ["rules", "claims", "occupies", "protects"] as const;

export const placeWorldWords = {
  heading: "🗺️ Land, climate, ruler",
  loading: "Loading…",
  failed: "Couldn't load this place's land, climate and ruler.",
  tryAgain: "Try again",
  land: {
    label: "Land",
    notSure: "Not sure",
    choices: {
      arctic: ["Ice and snow", "Arctic, glaciers, tundra"],
      coastal: ["Coast", "Shore, beach, sea cliffs"],
      desert: ["Desert", "Sand or bare rock"],
      forest: ["Forest", "Woods or jungle"],
      grassland: ["Grassland", "Plains, farms, meadows"],
      hill: ["Hills", "Rolling hills, moors"],
      mountain: ["Mountains", "Peaks and passes"],
      swamp: ["Swamp", "Marsh, bog, wetland"],
      underdark: ["Underdark", "Deep caves below the world"],
      urban: ["Town or city", "Streets and buildings"],
      waterborne: ["Open water", "Travel by boat or ship"],
    } as Record<string, [string, string]>,
    all: "All or most of it",
    part: "Only part of it",
  },
  climate: {
    label: "Climate",
    notSure: "Not sure",
    warmth: { arctic: "Freezing", cold: "Cold", temperate: "Mild", warm: "Warm", hot: "Hot" } as Record<string, string>,
    wetLabel: "Wet or dry?",
    neither: "Neither",
    dry: "and dry",
    wet: "and wet",
    // A smaller place has no climate of its own (it is the region's).
    tooSmall: "Only a region or bigger has its own climate. Set it on the region this place is in.",
  },
  ruler: {
    label: "Ruled by",
    notSure: "Not sure",
    howLabel: "How does it hold the place?",
    hows: { rules: "👑 Rules it", claims: "🚩 Claims it", occupies: "⚔️ Took it over", protects: "🛡️ Protects it" } as Record<string, string>,
    // The card's line for each way, as Discord has it.
    lines: { rules: "Ruled by", claims: "Claimed by", occupies: "Taken over by", protects: "Protected by" } as Record<string, string>,
    noGroups: "No group yet. Add the group as a name first.",
  },
  readMark: " (DMbot read this)",
  landLine: (word: string, part: boolean): string => `🗺️ Land: ${part ? "partly " : ""}${word}`,
  climateLine: (word: string, how: string): string => `Climate: ${word}${how ? ` and ${how}` : ""}`,
  rulerLine: (verb: string, group: string): string => `👑 ${verb}: ${group}`,
  hide: "🙈 Hide from players",
  show: "👁️ Show to players",
  nothingToShow: "Nothing to show yet. Set the land, climate or ruler first.",
  save: "Save",
  saveFirst: "Save your choices first, then you can hide or show them.",
  stillSaving: "Still saving that one. Press Save again for the rest.",
  working: "Saving…",
  notedLand: (name: string): string => `Noted: ${name}'s land is updated.`,
  notedClimate: (name: string): string => `Noted: ${name}'s climate is updated.`,
  notedRuler: (name: string): string => `Noted: ${name}'s ruler is updated.`,
  notedShown: (name: string, shown: boolean): string => (shown ? `Noted: players who can see ${name} see its land, climate and ruler.` : `Noted: ${name}'s land, climate and ruler are hidden from players.`),
} as const;

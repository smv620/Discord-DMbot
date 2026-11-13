// The words of a place's size, kind and setting on the Names page (#4187, #4173): the same words as Discord's place card, so the two
// stay level. A size is not a secret: anyone who can see the place sees it.
export const SIZES = ["plane", "world", "region", "settlement", "district", "street", "building", "room"] as const;
export type Size = (typeof SIZES)[number];
export const FORMS = ["water", "route"] as const;
export type Form = (typeof FORMS)[number];

export const placeWords = {
  sizeLabel: "📏 Size",
  save: "Save",
  // [the choice, its example] for each size, then Not sure.
  sizes: {
    plane: ["Plane", "Like the Feywild"],
    world: ["World or continent", "A whole world or landmass"],
    region: ["Region", "Kingdom, forest, island"],
    settlement: ["Town or city", "Also a village or port"],
    district: ["District", "A quarter, ward, docks"],
    street: ["Street or square", "Also a market or plaza"],
    building: ["Building", "Inn, temple, shop, castle"],
    room: ["Room", "Also a cellar or vault"],
  } as Record<Size, [string, string]>,
  notSure: ["Not sure", "No size: DMbot won't guess"] as [string, string],
  // How the size reads in a sentence ("Size: a town or city").
  sizeIn: {
    plane: "a plane",
    world: "a world or continent",
    region: "a region",
    settlement: "a town or city",
    district: "a district",
    street: "a street or square",
    building: "a building",
    room: "a room",
  } as Record<Size, string>,
  sizeLine: (size: Size): string => `Size: ${placeWords.sizeIn[size]}`,
  readNote: "(DMbot read this; change it if it's wrong)",
  noted: (name: string, size: Size): string => `Noted: ${name} is ${placeWords.sizeIn[size]}.`,
  notedNone: (name: string): string => `Noted: no size for ${name}.`,
  why: 'Then DMbot won\'t flag someone as "in two places" for being in a building and the town around it. Players who can see this place see its size too.',
  formAsk: "Land, water or a road?",
  forms: { land: ["Land or a building", "A town, a forest, an inn"], water: ["Water", "A lake, sea, bay or river"], route: ["A road or route", "A named road, trail or pass"] } as Record<string, [string, string]>,
  formLine: (form: Form): string => `🧭 Kind of place: ${form === "water" ? "water" : "a road or route"}`,
  notedForm: (name: string, form: Form | null): string => `Noted: ${name} is ${form === null ? "land or a building" : form === "water" ? "water" : "a road or route"}.`,
  settingMake: "🌍 Make it the setting",
  settingRemove: "🌍 Remove as setting",
  settingLine: "Campaign setting: the whole story happens here. DMbot counts smaller places as inside it unless the DM says otherwise.",
  notedSetting: (name: string): string => `Noted: ${name} is the campaign's setting.`,
  notedNoSetting: "Noted: no campaign setting now.",
  tooSmall: "Tap 📏 Size first and pick Region or bigger.",
  working: "Saving…",
} as const;

// "What's switched on" in a campaign's settings (#3693), in the words of Discord's Settings card button of the
// same name (core features.py). A list of names and plain sentences the bot wrote; no value, key or address.
export const featuresWords = {
  heading: "What's switched on",
  nothingOn: "Nothing extra is switched on yet. Whoever runs this DMbot can turn things on.",
  offHeading: "Not available right now",
  footer: "Whoever runs this DMbot can turn these on. Your campaign's own choices are in the settings above.",
  asOf: (when: string): string => `As DMbot found it when it last started, ${when}.`,
  on: "On",
  off: "Off",
} as const;

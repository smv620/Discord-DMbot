// The words while a DM-only panel on the recap page is fetched on demand (#2690): the page does not carry
// the panels up front, so each says it is coming (owner rule 2026-10-23: every wait shows it is working),
// says plainly when it is slow, and when it could not be fetched says what happened and what to do.
// (`what` is always written in lower case with "the", so it reads right inside each sentence.)
export const lazyWords = {
  loading: (what: string): string => `Loading ${what}…`,
  slow: (what: string): string => `Still loading ${what}. Your connection is slow. You can keep reading while you wait.`,
  stuck: (what: string): string => `${what.charAt(0).toUpperCase()}${what.slice(1)} still hasn't loaded. Tap Try again, or reload the page.`,
  failed: (what: string): string => `Couldn't load ${what}. Check your connection, then tap Try again. If that doesn't help, reload the page.`,
  retry: "Try again",
  reload: "Reload the page",
};

// The words while a DM-only panel on the recap page is fetched on demand (#2690): the page does not carry
// the panels up front, so each says it is coming (owner rule 2026-10-23: every wait shows it is working),
// says plainly when it is slow, and when it could not be fetched says what happened and what to do.
export const lazyWords = {
  loading: (what: string): string => `Loading ${what}…`,
  slow: (what: string): string => `Still loading ${what}… this is taking longer than usual.`,
  failed: (what: string): string => `${what} couldn't be loaded. Nothing was changed. Check your connection and try again.`,
  retry: "Try again",
};

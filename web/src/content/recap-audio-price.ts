// The narrated-recaps add-on's price, alone in a file with no imports so that both the pricing words
// (pricing.ts) and the add-on's words (recap-audio.ts, which imports pricing.ts) can read it without
// a cycle. The price is the owner's (#1385).

/** The add-on's price in cents, from the owner. null = not set: no price is shown anywhere, and nothing
 * says the add-on can be bought on the other plans (only that Guild and Pro include narration). */
export const RECAP_AUDIO_PRICE_CENTS: number | null = null;

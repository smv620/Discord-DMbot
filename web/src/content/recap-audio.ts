// The words and the price for the narrated-recaps add-on (#1390; owner decision on #1381): a
// narrator's voice and music for a recap, included in Guild and Pro and an add-on on the other paid
// plans. The rules are the API's (core/src/dmbot/entitlements.py RECAP_AUDIO_PLANS, web/me.py
// `recapAudio`, POST /billing/recap-audio); this file only says them in plain words.
//
// Everything here shows only while RECAP_AUDIO_LIVE is true (recap-words.ts): the audio is not
// built yet (#1383), and the site must not sell what does not exist. Turning it on also needs the
// price below, set by the owner (#1385).
import { formatPrice } from "./pricing";
import { RECAP_AUDIO_PRICE_CENTS } from "./recap-audio-price";

export { RECAP_AUDIO_PRICE_CENTS };

/** "$4.99 a month", or null while the owner has not set a price. */
export const priceText = (cents: number | null): string | null =>
  cents === null ? null : `${formatPrice(cents)} a month`;

export const price = (): string | null => priceText(RECAP_AUDIO_PRICE_CENTS);

/** The pricing page's "Add-ons" part. The owner's headline "Every plan has everything." stays: this is
 * an extra, not part of any plan, and the FAQ says so in the same words (content/pricing.ts). */
export const pricingRow = {
  heading: "Add-ons",
  lead: "Extras are not part of any plan. Guild and Pro include this one.",
  name: "Narrated recaps with music",
  priceLine: price(),
  // Without a price nothing offers the add-on to the other plans: a card that offers something and says
  // nothing of the cost is not honest (#1390).
  included:
    RECAP_AUDIO_PRICE_CENTS === null
      ? "Comes with Guild and Pro. The other plans do not have it."
      : "Comes with Guild and Pro. Add it to Table or Two Tables by the month.",
  line: "After a session, DMbot reads your recap aloud in a narrator's voice, with music behind it. You decide what your table hears.",
};

/** My Account's part and the recap page's line. */
export const accountWords = {
  heading: "Narrated recaps with music",
  included: "Your plan includes narrated recaps.",
  addOn: (endsOn: string | undefined): string =>
    endsOn
      ? `Narrated recaps are on, paid through ${endsOn}. To stop them, press Change plan.`
      : "Narrated recaps are on. To stop them, press Change plan.",
  buy: "Add narrated recaps",
  buyNote: (cost: string | null): string =>
    `${cost ? `${cost}. ` : ""}You pay on our payment company's page. It renews each month until you stop it. To stop it, press Change plan.`,
  notIncluded: "Narrated recaps with music are an extra for your plan.",
  errors: {
    "already-included": "Your plan already includes narrated recaps.",
    "already-subscribed": "You already have narrated recaps. Nothing to pay.",
  },
};

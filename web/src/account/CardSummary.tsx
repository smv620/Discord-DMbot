/** @jsxImportSource preact */
// The three lines at the top of a character's card (#5051): who they are, first met, last mentioned. Each draws only when the API
// sends it; while DMbot is still writing the sentence, one line says so. Loaded only while CARD_SUMMARY_LIVE is on.
import { CARD_SUMMARY_LIVE } from "../content/card-summary-switch";
import { cardSummaryWords as w } from "../content/card-summary-words";
import type { CardSummaryFields } from "./api";

export default function CardSummary({ card }: { card: CardSummaryFields }) {
  if (!CARD_SUMMARY_LIVE) return null;
  const lines: string[] = [];
  if (card.whoPending) lines.push(w.pending);
  else if (card.who) lines.push(card.who);
  if (card.firstMet) lines.push(w.firstMet(card.firstMet));
  if (card.lastMentioned) lines.push(w.lastMentioned(card.lastMentioned));
  if (lines.length === 0) return null;
  return (
    <div class="stack">
      {lines.map((line) => (
        <p class="small" key={line}>
          {line}
        </p>
      ))}
    </div>
  );
}

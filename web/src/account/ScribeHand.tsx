/** @jsxImportSource preact */
// A hand writing with a quill and turning a page, shown while DMbot is writing a recap (owner request, 2026-11-09), with one
// line saying what it is thinking about at the step it is on. The drawing is decoration: hidden from screen readers (the stage
// line is the words) and still for anyone who asked for less motion (the page's reduced-motion rule is in account.css).
// `moving` is false while the work has stalled, so a hand that keeps writing never sits beside a "nothing new" note.
// The thinking lines are fixed words for each step of the recap (content/recaps.ts, `scribe`): DMbot does not make them up as
// it goes, and a step with no line shows only the hand.
import { text } from "../content/recaps";

export function scribeLine(stage: string | null | undefined): string | null {
  return stage && Object.hasOwn(text.scribe, stage) ? (text.scribe[stage] as string) : null;
}

export default function ScribeHand({ stage, moving }: { stage?: string | null | undefined; moving: boolean }) {
  const line = scribeLine(stage);
  return (
    <div class={moving ? "scribe scribe-moving" : "scribe"}>
      <svg class="scribe-art" viewBox="0 0 120 72" width="120" height="72" aria-hidden="true" focusable="false">
        {/* the page being written on, and the leaf that turns over it */}
        <rect x="8" y="8" width="72" height="56" rx="3" class="scribe-page" />
        <path d="M20 26h48M20 38h48M20 50h30" class="scribe-lines" />
        <path class="scribe-ink" d="M20 26h48" pathLength="100" />
        <rect x="44" y="8" width="36" height="56" rx="3" class="scribe-leaf" />
        {/* the hand and its quill: the nib is at the group's origin, which moves along the line being written */}
        <g class="scribe-hand" transform="translate(20 26)">
          <g class="scribe-hand-move">
            <path d="M0 0L30-30" class="scribe-shaft" />
            <path d="M14-16C20-36 40-46 56-44C54-30 44-16 24-8Z" class="scribe-quill" />
            <path d="M4 2C2-8 10-16 22-14C34-12 40-4 34 6C28 14 8 12 4 2Z" class="scribe-skin" />
            <path d="M24-14c3-4 8-3 8 1M29-10c3-4 8-2 7 2M32-4c3-3 7-1 6 3" class="scribe-knuckle" />
          </g>
        </g>
      </svg>
      {line && <p class="scribe-line">{line}</p>}
    </div>
  );
}

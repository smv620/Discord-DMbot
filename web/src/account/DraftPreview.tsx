/** @jsxImportSource preact */
// A recap draft that DMbot is still checking (#3861, the owner's words): the text is faded to about half, so it reads as not
// yet final, and a large light-grey "DRAFT" runs diagonally over it. The mark is an overlay: it is not in the text, it does
// not catch the mouse or the finger, and the text under it can still be selected and read (a screen reader hears a plain
// "Draft, still being checked." note and then the text, instead of the letters). Once the recap
// is final `checking` is false and this draws the text as it was, with no fade and no mark.
import type { ComponentChildren } from "preact";

import { text } from "../content/recaps";

export default function DraftPreview({ checking, children }: { checking: boolean; children: ComponentChildren }) {
  if (!checking) return <>{children}</>;
  return (
    <div class="draft-preview">
      <span class="visually-hidden">{text.draftNote}</span>
      <div class="draft-preview-text">{children}</div>
      <span class="draft-preview-mark" aria-hidden="true">
        {text.draftMark}
      </span>
    </div>
  );
}

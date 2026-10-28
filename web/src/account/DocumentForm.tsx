/** @jsxImportSource preact */
// The "Use a document link" panel (#2843): a player gives DMbot a public web page or PDF they made, when they have no
// sheet link. The shape is checked by checkDocumentLink and the page queues it; the bot fetches it later, never the page.
import { sheetWords as w } from "../content/sheets-words";
import type { SheetCharacter } from "./api";

export default function DocumentForm({
  sheet,
  value,
  busy,
  onValue,
  onSubmit,
}: {
  sheet: SheetCharacter;
  value: string;
  busy: boolean;
  onValue: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <details>
      <summary>{w.documentSummary}</summary>
      {/* noValidate: the browser's own message for a bad link would replace the plain words the page gives. */}
      <form
        class="stack"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <fieldset class="stack">
          <legend class="visually-hidden">{w.documentFor(sheet.name)}</legend>
          <label>
            {w.documentLabel(sheet.name)}
            {/* No maxLength: a browser would cut a long paste to 400 characters without a word, and the too-long check
                would never see it. The check gives the plain words instead. */}
            <input
              type="url"
              value={value}
              onInput={(e) => onValue((e.target as HTMLInputElement).value)}
            />
          </label>
          <p class="small muted">{w.documentHint}</p>
        </fieldset>
        {sheet.source !== null && <p class="small muted">{w.documentReplaces}</p>}
        <button type="submit" class="button" aria-disabled={busy}>
          {w.documentRead}
        </button>
      </form>
    </details>
  );
}

/** @jsxImportSource preact */
// The website's search bar, line 1 (#3855): a 🔍 in the header opens a box; as someone types, the page that fits best shows at
// once, with one short line on what it is for. It is matched here in the browser on every keystroke (src/search/match.ts):
// nothing is sent anywhere, and the words typed are kept only while the box is open. A page for signed-in people is linked the
// same way; the account pages send a visitor to sign in first. Escape closes it and puts the keyboard back on the 🔍. The
// result is in a live region that is always on the page, so a screen reader hears it change. Line 2 (the short answer from
// DMbot) comes later, with its own switch.
import { useEffect, useRef, useState } from "preact/hooks";

import { searchWords as w } from "../content/search-words";
import { bestMatch } from "./match";

export default function SearchBar() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const opener = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);
  const found = text.trim() === "" ? null : bestMatch(text);
  const close = (): void => {
    setOpen(false);
    setText("");
    opener.current?.focus();
  };
  return (
    <div class="site-search">
      <button
        ref={opener}
        type="button"
        class="site-search-open"
        aria-expanded={open}
        aria-controls="site-search-panel"
        aria-label={open ? w.close : w.open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span aria-hidden="true">🔍</span>
      </button>
      <div id="site-search-panel" class="site-search-panel" hidden={!open} onKeyDown={(e) => e.key === "Escape" && close()}>
        <label for="site-search-field">{w.label}</label>
        <input
          id="site-search-field"
          ref={field}
          type="search"
          autocomplete="off"
          value={text}
          placeholder={w.hint}
          onInput={(e) => setText((e.target as HTMLInputElement).value)}
        />
        <div role="status" aria-live="polite" class="site-search-result">
          {text.trim() !== "" &&
            (found ? (
              <p>
                <span class="muted">{w.best}</span> <a href={found.path}>{found.title}</a>
                <br />
                <span class="small muted">{found.about}</span>
              </p>
            ) : (
              <p class="muted">{w.none}</p>
            ))}
        </div>
      </div>
    </div>
  );
}

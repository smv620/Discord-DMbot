/** @jsxImportSource preact */
// The website's search bar, line 1 (#3855): a 🔍 in the header opens a box; as someone types, the page that fits best shows at
// once, with one short line on what it is for. It is matched here in the browser on every keystroke (src/search/match.ts):
// nothing is sent anywhere, and the words typed are kept only while the box is open. A page for signed-in people is linked the
// same way; the account pages send a visitor to sign in first. Escape closes it and puts the keyboard back on the 🔍. The
// result is in a live region that is always on the page, so a screen reader hears it change. Line 2 (the short answer from
// DMbot) comes later, with its own switch.
import { useEffect, useRef, useState } from "preact/hooks";

import { SEARCH_ASK_LIVE } from "../content/search-ask-switch";
import { searchWords as w } from "../content/search-words";
import { type Asked, askSite, ASK_MAX } from "./ask";
import { bestMatch } from "./match";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function SearchBar() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const opener = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  // Line 2 (#3855): null until a question is asked; "thinking" while it is with DMbot, with the seconds counting.
  const [line2, setLine2] = useState<Asked | "thinking" | null>(null);
  const [waited, setWaited] = useState(0);
  const asking = useRef<AbortController | null>(null);
  useEffect(() => {
    if (line2 !== "thinking") return;
    setWaited(0);
    const timer = setInterval(() => setWaited((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [line2]);
  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);
  const found = text.trim() === "" ? null : bestMatch(text);
  const ask = async (): Promise<void> => {
    if (!SEARCH_ASK_LIVE || text.trim() === "" || line2 === "thinking") return;
    const mine = new AbortController();
    asking.current = mine;
    setLine2("thinking");
    const got = await askSite(text, base, fetch, mine.signal);
    if (asking.current === mine) setLine2(got);
  };
  const close = (): void => {
    asking.current?.abort();
    asking.current = null;
    setLine2(null);
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
          maxLength={SEARCH_ASK_LIVE ? ASK_MAX : undefined}
          onInput={(e) => {
            setText((e.target as HTMLInputElement).value);
            if (line2 !== "thinking") setLine2(null); // (an answer is for the question it was asked of)
          }}
          onKeyDown={(e) => {
            if (SEARCH_ASK_LIVE && e.key === "Enter") {
              e.preventDefault();
              void ask();
            }
          }}
        />
        {SEARCH_ASK_LIVE && (
          <button type="button" class="button secondary" disabled={text.trim() === "" || line2 === "thinking"} onClick={() => void ask()}>
            {w.ask}
          </button>
        )}
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
        {SEARCH_ASK_LIVE && (
          <div role="status" aria-live="polite" class="site-search-answer">
            {line2 === "thinking" && (
              <p class="muted">
                {w.thinking} {waited >= 1 && <span class="small">{w.seconds(waited)}</span>} {waited >= 20 && <span>{w.stillThinking}</span>}
              </p>
            )}
            {line2 !== null && line2 !== "thinking" && line2.kind === "answer" && (
              <p>
                {line2.answer}
                {line2.link && (
                  <>
                    {" "}
                    <a href={line2.link}>{line2.link}</a>
                  </>
                )}
              </p>
            )}
            {line2 !== null && line2 !== "thinking" && line2.kind === "limit" && <p class="muted">{w.limit[line2.limit]}</p>}
            {line2 !== null && line2 !== "thinking" && line2.kind === "failed" && <p class="muted">{w.cant}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

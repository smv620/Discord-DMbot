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
/** How long typing must pause before the question is asked. */
export const PAUSE_MS = 600;
/** The server gives up at 10 s, so a reply after 12 s is not coming: stop waiting and say so. */
export const GIVE_UP_MS = 12_000;

export default function SearchBar() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const opener = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  // Line 2 (#3855): null until a question is asked; "thinking" while it is with DMbot, with the seconds counting.
  const [line2, setLine2] = useState<Asked | "thinking" | null>(null);
  const [waited, setWaited] = useState(0);
  const asking = useRef<AbortController | null>(null);
  const lastAsked = useRef("");
  useEffect(() => {
    if (line2 !== "thinking") return;
    setWaited(0);
    const timer = setInterval(() => setWaited((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [line2]);
  // Ask once typing has paused for 600 ms (the owner's request: no button to press); a pause with changed words drops the
  // earlier call and asks again, and the same words are not asked twice. Enter asks at once.
  useEffect(() => {
    if (!SEARCH_ASK_LIVE || !open) return;
    const words = text.trim();
    if (words === "") {
      asking.current?.abort();
      asking.current = null;
      lastAsked.current = "";
      setLine2(null);
      return;
    }
    if (words === lastAsked.current) return;
    const pause = setTimeout(() => void ask(), PAUSE_MS);
    return () => clearTimeout(pause);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, open]);
  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);
  const found = text.trim() === "" ? null : bestMatch(text);
  const ask = async (): Promise<void> => {
    const words = text.trim();
    if (!SEARCH_ASK_LIVE || words === "" || words === lastAsked.current) return;
    asking.current?.abort(); // (changed words drop the earlier call)
    const mine = new AbortController();
    asking.current = mine;
    lastAsked.current = words;
    setLine2("thinking");
    const giveUp = setTimeout(() => {
      mine.abort();
      if (asking.current === mine) {
        setLine2({ kind: "failed" });
        lastAsked.current = ""; // (so Enter or Ask can try the same words again)
      }
    }, GIVE_UP_MS);
    const got = await askSite(words, base, fetch, mine.signal);
    clearTimeout(giveUp);
    if (asking.current === mine) {
      setLine2(got);
      if (got.kind === "failed") lastAsked.current = "";
    }
  };
  const close = (): void => {
    asking.current?.abort();
    asking.current = null;
    lastAsked.current = "";
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
          }}
          onKeyDown={(e) => {
            if (SEARCH_ASK_LIVE && e.key === "Enter") {
              e.preventDefault();
              void ask();
            }
          }}
        />
        {SEARCH_ASK_LIVE && (
          <button type="button" class="button secondary" disabled={text.trim() === ""} onClick={() => void ask()}>
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
                {waited >= 3 ? w.thinkingFor(waited) : w.thinking}
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

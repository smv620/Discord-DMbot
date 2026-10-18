// The Help page's search box (#1939). Everything here runs in the reader's browser and nothing is
// sent anywhere or kept: the box filters the steps already on the page. The matching and the
// show-and-hide are plain functions so the vitest can run them on a rendered page.

// Emoji and joiners carry no word, so "stop recording" finds a step that says "⚙️ Menu … Stop recording me".
const emoji = /[\p{Extended_Pictographic}️‍]/gu;

/** Lower case, no emoji, only letters and numbers, one space between words. */
export function normalize(text) {
  return text
    .replace(emoji, " ")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** Does every word the reader typed appear in the text? (Typing nothing matches everything.) */
export function matches(query, text) {
  const words = normalize(query).split(" ").filter(Boolean);
  if (words.length === 0) return true;
  const haystack = normalize(text);
  return words.every((w) => haystack.includes(w));
}

export const nothingFound = "Nothing found. Try another word, or see all steps.";

/** What the polite live region says. */
export function statusText(query, found) {
  if (normalize(query) === "") return "";
  if (found === 0) return nothingFound;
  return `${found} ${found === 1 ? "step" : "steps"} found`;
}

const mark = "data-search-hid";

function hide(el) {
  el.hidden = true;
  el.setAttribute(mark, "");
}

/**
 * Show the steps (list items inside the page's sections) that match, under their section headings;
 * hide the rest, and the lead-ins and notes of a section while a search is on. An empty query puts
 * the page back as it was. Returns how many steps match.
 */
export function applySearch(root, query) {
  for (const el of root.querySelectorAll(`[${mark}]`)) {
    el.hidden = false;
    el.removeAttribute(mark);
  }
  if (normalize(query) === "") return 0;

  let found = 0;
  for (const section of root.querySelectorAll("section")) {
    for (const li of section.querySelectorAll("li")) {
      if (matches(query, li.textContent ?? "")) found++;
      else hide(li);
    }
    for (const child of section.children) {
      if (child.hidden) continue;
      const tag = child.tagName;
      if (tag === "H2") continue;
      if (tag === "UL" || tag === "OL") {
        if (child.querySelector("li:not([hidden])") === null) hide(child);
      } else if (tag === "H3") {
        const next = child.nextElementSibling;
        if (next === null || !["UL", "OL"].includes(next.tagName) || next.querySelector("li:not([hidden])") === null) hide(child);
      } else {
        hide(child);
      }
    }
    if (section.querySelector("li:not([hidden])") === null) hide(section);
  }
  return found;
}

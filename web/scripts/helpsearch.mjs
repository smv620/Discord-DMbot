// The Help page's search box (#1939). Everything here runs in the reader's browser and nothing is
// sent anywhere or kept: the box filters the steps already on the page. The matching and the
// show-and-hide are plain functions so the vitest can run them on a rendered page.

// Emoji and joiners carry no word, so "stop recording" finds a step that says "🗂️ Menu … Stop recording me".
// (Written as escapes so an editor can't strip the invisible ones. ©, ® and ™ count as emoji here too,
// which is harmless: no step turns on them.)
const emoji = /[\p{Extended_Pictographic}\uFE0F\u200D]/gu;

/**
 * Lower case, no emoji, no accents, only letters and numbers, one space between words. An apostrophe
 * joins ("don't" is "dont", not "don" and "t", and "t" would match almost anything).
 */
export function normalize(text) {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .replace(emoji, " ")
    .toLowerCase()
    .replace(/['\u2019]/g, "")
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

  // (The consent promises count as steps too, so they can be found without their lead-in.)
  let found = 0;
  for (const section of root.querySelectorAll("section")) {
    for (const li of section.querySelectorAll("li")) {
      if (matches(query, li.textContent ?? "")) found++;
      else hide(li);
    }
    for (const child of section.children) {
      if (child.hidden) continue;
      const tag = child.tagName;
      if (tag === "H2" || tag === "SPAN" || child.classList.contains("copy-link")) continue; // (a span is an old address)
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
  // A contents link to a section that is hidden would jump nowhere.
  for (const a of root.querySelectorAll('nav a[href^="#"]')) {
    const target = root.ownerDocument?.getElementById(a.getAttribute("href").slice(1)) ?? null;
    if (target !== null && target.closest("[hidden]") !== null) {
      const item = a.closest("li") ?? a;
      hide(item);
    }
  }
  return found;
}

/**
 * Connect the box to the page: typing filters at once, the count is spoken a moment after the last
 * key (so fast typing isn't read out letter by letter), Esc and the button clear it, and Enter
 * doesn't submit anything. Returns the function that runs a search, for the tests.
 */
export function wire(form, page, wait = 300) {
  const input = form.querySelector("input");
  const status = form.querySelector("#help-search-status");
  const clear = form.querySelector("[data-help-search-clear]");
  let timer;
  const run = (now = false) => {
    const found = applySearch(page, input.value);
    clear.hidden = !(normalize(input.value) !== "" && found === 0);
    const say = () => {
      status.textContent = statusText(input.value, found);
    };
    clearTimeout(timer);
    if (now || normalize(input.value) === "") say();
    else timer = setTimeout(say, wait);
  };
  form.hidden = false;
  // A section or step reached by its link is highlighted; typing in the box clears that, and a new
  // link brings it back.
  const view = page.ownerDocument?.defaultView;
  view?.addEventListener("hashchange", () => page.removeAttribute("data-quiet"));
  // A link to the place the address already names fires no hashchange, so clicks count too.
  page.ownerDocument?.addEventListener("click", (e) => {
    if (e.target?.closest?.('a[href^="#"]')) page.removeAttribute("data-quiet");
  });
  input.addEventListener("input", () => {
    page.setAttribute("data-quiet", "");
    run();
  });
  form.addEventListener("submit", (e) => e.preventDefault());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && input.value !== "") {
      e.preventDefault();
      input.value = "";
      run(true);
    }
  });
  clear.addEventListener("click", () => {
    input.value = "";
    run(true);
    input.focus();
  });
  return run;
}

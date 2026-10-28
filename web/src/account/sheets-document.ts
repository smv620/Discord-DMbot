// The "Use a document link" check (#2843). A pure function: the page checks only the shape of the link, and the bot
// fetches and reads the document later. Discord's field holds 400 characters, so the same limit is kept here.
export type DocumentLinkProblem = "empty" | "not-web" | "too-long";
export type DocumentLinkCheck = { ok: true; url: string } | { ok: false; problem: DocumentLinkProblem };

export const DOCUMENT_LINK_MAX = 400;

// Characters a browser or the URL parser would quietly drop or keep inside the link (tabs, newlines, NUL and the
// other control codes). Refused outright, so what is sent is exactly what the player typed.
const CONTROL = /[\u0000-\u001f\u007f]/;

/** Trims the link first. It must be an http or https address with a host and no spaces. */
export function checkDocumentLink(text: string): DocumentLinkCheck {
  const url = text.trim();
  if (url === "") return { ok: false, problem: "empty" };
  if (url.length > DOCUMENT_LINK_MAX) return { ok: false, problem: "too-long" };
  // The literal prefix comes first: the URL parser would read "http:/example.com" as a web address, and the words say
  // the link must start with https:// or http://.
  if (!/^https?:\/\//i.test(url)) return { ok: false, problem: "not-web" };
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, problem: "not-web" };
  }
  const web = parsed.protocol === "https:" || parsed.protocol === "http:";
  if (!web || parsed.host === "" || /\s/.test(url) || CONTROL.test(url)) return { ok: false, problem: "not-web" };
  return { ok: true, url };
}

// The "Use a document link" check (#2843). A pure function: the page checks only the shape of the link, and the bot
// fetches and reads the document later. Discord's field holds 400 characters, so the same limit is kept here.
export type DocumentLinkCheck = { ok: true; url: string } | { ok: false; problem: "empty" | "not-web" | "too-long" };

export const DOCUMENT_LINK_MAX = 400;

/** Trims the link first. It must be an http or https address with a host and no spaces. */
export function checkDocumentLink(text: string): DocumentLinkCheck {
  const url = text.trim();
  if (url === "") return { ok: false, problem: "empty" };
  if (url.length > DOCUMENT_LINK_MAX) return { ok: false, problem: "too-long" };
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, problem: "not-web" };
  }
  const web = parsed.protocol === "https:" || parsed.protocol === "http:";
  if (!web || parsed.host === "" || /\s/.test(url)) return { ok: false, problem: "not-web" };
  return { ok: true, url };
}

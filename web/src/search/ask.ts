// The search bar's short answer (#3855 line 2; core #3856, POST /site/ask): the question goes to DMbot and an answer comes
// back, `{answer, link}`, or a 429 `{limit: "hour" | "day"}`. No sign-in, no cookie: the call is for anyone. It sends the
// header every call to the API sends, and nothing is kept on the page after the box is closed. The answer is plain text; its
// one link is used only if it is a page of this website (a path, or an address on getdmbot.com), so a link the model made up
// cannot take a visitor anywhere else.
export type Asked =
  | { kind: "answer"; answer: string; link: string | null }
  | { kind: "limit"; limit: "hour" | "day" }
  | { kind: "failed" };

/** The longest question the route takes. */
export const ASK_MAX = 300;

/** A page of this website, as a path; or null. */
export function ownPage(link: unknown): string | null {
  if (typeof link !== "string") return null;
  const text = link.trim();
  if (/^\/(?!\/)[\w\-./#?=&%]*$/.test(text)) return text;
  const full = /^https:\/\/(?:www\.)?getdmbot\.com(\/[\w\-./#?=&%]*)?$/.exec(text);
  return full ? (full[1] ?? "/") : null;
}

export async function askSite(question: string, base: string, fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<Asked> {
  const q = question.trim().slice(0, ASK_MAX);
  if (q === "") return { kind: "failed" };
  try {
    const init: RequestInit = {
      method: "POST",
      credentials: "omit",
      headers: { "X-DMbot-Request": "1", Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ q }),
    };
    if (signal) init.signal = signal;
    const response = await fetcher(`${base}/site/ask`, init);
    if (response.status === 429) {
      const body = (await response.json().catch(() => ({}))) as { limit?: unknown };
      return { kind: "limit", limit: body.limit === "day" ? "day" : "hour" };
    }
    if (!response.ok) return { kind: "failed" };
    const body = (await response.json()) as { answer?: unknown; link?: unknown };
    if (typeof body.answer !== "string" || body.answer.trim() === "") return { kind: "failed" };
    return { kind: "answer", answer: body.answer, link: ownPage(body.link) };
  } catch {
    return { kind: "failed" };
  }
}

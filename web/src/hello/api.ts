/**
 * Sending the "Say hello" forms to the web API (#665, POST /feedback in core). No sign-in:
 * the API posts the message as a GitHub Discussion and keeps the contact with the team.
 * Like every API call, it sends the X-DMbot-Request header, which a plain cross-site
 * form can't.
 */
export type Kind = "feedback" | "question";

export interface Note {
  kind: Kind;
  message: string;
  contact: string;
  /** Cloudflare Turnstile's token, or "" when the site has no Turnstile key. */
  turnstile: string;
}

/** "sent", or why not, in a form the page turns into plain words. */
export type SendResult =
  | "sent"
  | "empty"
  | "too-long"
  | "contact-too-long"
  | "slow-down"
  | "not-human"
  | "failed";

export type Send = (note: Note) => Promise<SendResult>;

const results: Record<string, SendResult> = {
  empty: "empty",
  too_long: "too-long",
  contact_too_long: "contact-too-long",
  slow_down: "slow-down",
  not_human: "not-human",
};

/** The real API. `base` is its address, e.g. "https://api.example" or "/api". */
export function httpSend(base: string, fetcher: typeof fetch = fetch): Send {
  const root = base.replace(/\/+$/, "");
  return async (note) => {
    let response: Response;
    try {
      response = await fetcher(`${root}/feedback`, {
        method: "POST",
        // Nothing to sign in to: no cookie is needed or sent.
        credentials: "omit",
        headers: {
          "X-DMbot-Request": "1",
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          kind: note.kind,
          message: note.message,
          contact: note.contact.trim() || null,
          turnstile: note.turnstile,
        }),
      });
    } catch {
      return "failed";
    }
    if (response.ok) return "sent";
    try {
      const code = String(((await response.json()) as { error?: unknown }).error ?? "");
      // Anything else (forms switched off, GitHub down, a server error): try later.
      return results[code] ?? "failed";
    } catch {
      return "failed";
    }
  };
}

/** The pretend API for preview builds (PUBLIC_API_BASE=mock): every message "sends". */
export const pretendSend: Send = async () => "sent";

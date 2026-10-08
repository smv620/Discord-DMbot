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

/** Why a message wasn't sent, in a form the page turns into plain words. */
export type Refusal =
  | "empty"
  | "too-long"
  | "contact-too-long"
  | "slow-down"
  | "busy"
  | "not-human"
  | "off"
  | "failed";

/** Sent, with the public post's address; or why not. */
export type SendResult = { sent: true; url: string } | { sent: false; why: Refusal };

export type Send = (note: Note) => Promise<SendResult>;

const refusals: Record<string, Refusal> = {
  empty: "empty",
  too_long: "too-long",
  contact_too_long: "contact-too-long",
  slow_down: "slow-down",
  busy: "busy",
  not_human: "not-human",
  feedback_off: "off",
};

const no = (why: Refusal): SendResult => ({ sent: false, why });

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
        // Never leave "Sending…" up for good if the API hangs.
        signal: AbortSignal.timeout(30_000),
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
      return no("failed");
    }
    try {
      const answer = (await response.json()) as { url?: unknown; error?: unknown };
      if (response.ok && typeof answer.url === "string") return { sent: true, url: answer.url };
      // Anything else (GitHub down, a server error): try later.
      return no(refusals[String(answer.error ?? "")] ?? "failed");
    } catch {
      return no("failed");
    }
  };
}

/** The pretend API for preview builds (PUBLIC_API_BASE=mock): every message "sends". */
export const pretendSend: Send = async () => ({
  sent: true,
  url: "https://github.com/smv620/Discord-DMbot/discussions",
});

// The hand-over link's code, kept across the sign-in (#2534). A file of its own so My Account, which only asks
// "is there a code waiting?", does not load the whole accept page to find out (#2743).
const KEY = "dmbot:handover";

/** The token in the address's hash, or one kept across the sign-in; null if neither looks right. */
export function handoverToken(hash: string): string | null {
  const fromHash = hash.replace(/^#/, "");
  if (/^[\w-]{1,200}$/.test(fromHash)) return fromHash;
  try {
    const kept = sessionStorage.getItem(KEY);
    return kept && /^[\w-]{1,200}$/.test(kept) ? kept : null;
  } catch {
    return null;
  }
}

export function keepHandoverToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // Storage may be blocked; the link in the email still works.
  }
}

export function forgetHandoverToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing to forget
  }
}

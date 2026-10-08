/**
 * The account page only ever sends the browser to Discord (sign-in, adding DMbot) or to
 * the payment company (checkout, billing). Any other address from the API is refused, so
 * a bug or a tampered response can't send people to a look-alike site.
 */
const allowedHosts = ["discord.com", "paddle.com", "lemonsqueezy.com"];

export function isSafeRedirect(url: string): boolean {
  // The pretend API (mock builds) returns same-page links like "#demo-checkout-table".
  if (url.startsWith("#")) return true;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase();
  return allowedHosts.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
}

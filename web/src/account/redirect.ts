/**
 * The account page only ever sends the browser to Discord (sign-in, adding DMbot) or to
 * the payment company (checkout, billing). Any other address from the API is refused, so
 * a bug or a tampered response can't send people to a look-alike site.
 */
const allowedHosts = ["discord.com", "paddle.com", "lemonsqueezy.com"];

/** True only in a preview build with the pretend API (PUBLIC_API_BASE=mock). */
const mockBuild = import.meta.env.PUBLIC_API_BASE === "mock";

export function isSafeRedirect(url: string, allowSamePage: boolean = mockBuild): boolean {
  // The pretend API returns same-page links like "#demo-checkout-table"; a real build
  // never follows one.
  if (url.startsWith("#")) return allowSamePage;
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

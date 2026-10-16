// The one call behind a personal link (#1231): the API checks the signed code in the address and
// answers with the person's Discord name and experience points, or with a plain "no".
export interface Personal {
  name: string | null;
  experiencePoints: number;
}

/** The person this link is for, or null for anything else: no code, a wrong or ended one, an API
 * that is down or older than this. It never throws, so the page shows its plain version. */
export async function fetchPersonal(
  base: string,
  uid: string,
  token: string,
  fetcher: typeof fetch = fetch,
): Promise<Personal | null> {
  const root = base.replace(/\/+$/, "");
  const query = new URLSearchParams({ uid, t: token });
  try {
    // No cookies: the code in the link is all this needs, and it must work signed out.
    const response = await fetcher(`${root}/personal?${query}`, {
      credentials: "omit",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const body = (await response.json()) as Partial<Personal>;
    if (typeof body.experiencePoints !== "number") return null;
    return {
      name:
        typeof body.name === "string" && body.name !== "" ? body.name : null,
      experiencePoints: body.experiencePoints,
    };
  } catch {
    return null;
  }
}

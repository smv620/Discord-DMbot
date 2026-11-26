// The Shared story adventure's calls on the website (#3694, behind ADVENTURE_LIVE), loaded only when the Upload lore page opens "Your adventure", so
// their method names stay out of the first download (page weight). Same shape as the other area files: given the helpers, it returns the calls.
import type { AdventureApi, ApiErrorKind, AdventureGive, AdventureGuide, AdventureList, AdventureRead, AdventureReadStart, AdventureRemoval, ApiHelpers } from "./api";

// The refusals only these calls know, worded by the page: kept here so the first download does not carry them.
const kinds: Record<string, ApiErrorKind> = { not_on: "not-on", not_guide: "not-on", not_readable: "not-readable" };

export default function adventureArea({ call: send }: ApiHelpers): AdventureApi {
  const call = (method: "GET" | "POST", path: string, body?: unknown, raw?: Blob): Promise<unknown> => send(method, path, body, false, raw, false, kinds);
  return {
    adventure: async (ref) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure`)) as AdventureList,
    adventureRead: async (ref, offset) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure/read?offset=${offset}`)) as AdventureRead,
    // The file goes as it is, not as JSON (its name in the address): the API reads it with a running cap, as the lore PDF's does.
    adventureGive: async (ref, file, name) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/adventure/give?name=${encodeURIComponent(name)}`, undefined, new Blob([file], { type: "application/octet-stream" }))) as { give: string },
    adventureGuide: async (ref, document, job, chapter) =>
      (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/adventure/${encodeURIComponent(document)}/guide?job=${job}${chapter ? `&chapter=${encodeURIComponent(chapter)}` : ""}`)) as { guide: string },
    adventureGuiding: async (ref, guide) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure/guides/${encodeURIComponent(guide)}`)) as AdventureGuide,
    adventureStartRead: async (ref, document) => (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/adventure/${encodeURIComponent(document)}/read`)) as { read: string },
    adventureStarting: async (ref, read) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure/reads/${encodeURIComponent(read)}`)) as AdventureReadStart,
    adventureGiving: async (ref, give) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure/gives/${encodeURIComponent(give)}`)) as AdventureGive,
    adventureRemove: async (ref, document) => (await call("POST", `/me/campaigns/${encodeURIComponent(ref)}/adventure/${encodeURIComponent(document)}/remove`)) as { removal: string },
    adventureRemoval: async (ref, removal) => (await call("GET", `/me/campaigns/${encodeURIComponent(ref)}/adventure/removals/${encodeURIComponent(removal)}`)) as AdventureRemoval,
  };
}

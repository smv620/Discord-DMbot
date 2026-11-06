// Story Graph node ids are opaque (#3085): most are an entry's id (32 hex), but a person a character knows only by a
// secret other name is a second node whose id is "alias:<32 hex>". It has no NPC page of its own, so nothing here
// parses an id or builds a link from one except through `npcPageHref`, which gives no link for an alias (the
// API's `page` flag is not the only guard). Re-centring on any id, an alias included, is the door's job.
const ALIAS_PREFIX = "alias:";

/** Whether the id is a node that stands for a secret other name rather than an entry of its own. */
export function isAliasNode(id: string): boolean {
  return id.startsWith(ALIAS_PREFIX);
}

/** The address of a node's NPC page, or null when it has none (an alias node). `ref` is `<server>.<campaign>`. */
export function npcPageHref(ref: string, id: string): string | null {
  if (isAliasNode(id)) return null;
  return `/account/npc/${encodeURIComponent(ref)}/${encodeURIComponent(id)}`;
}

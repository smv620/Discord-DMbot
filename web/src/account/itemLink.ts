// "Open on the website" in a private message links to a page with `?item=<id>` (#3663): the page opens on that item, or says it
// has been answered. The value is an id only (never a name or any words), so the address holds nothing readable; anything
// that is not an id is ignored. Pure helpers here, so every page reads the link the same way.

/** An item id as the pages know them: letters, digits and `. - _ :`, up to 80 (a pair of entry ids joined by a dot fits). */
const ITEM = /^[A-Za-z0-9._:-]{1,80}$/;

/** The `item` of an address's query string (`?item=…`), or null when there is none or it is not an id. */
export function itemFromSearch(search: string): string | null {
  if (!search.startsWith("?")) return null;
  const value = new URLSearchParams(search.slice(1)).get("item")?.trim() ?? "";
  return ITEM.test(value) ? value : null;
}

/** The item of the page the person is on now; null on the server or without one. */
export function currentItem(): string | null {
  return typeof window === "undefined" ? null : itemFromSearch(window.location.search);
}

/** A line's two names from an item "<idA>.<idB>" (32 hex digits each), or null. */
export function pairFromItem(item: string): [string, string] | null {
  const m = /^([0-9a-f]{32})\.([0-9a-f]{32})$/.exec(item);
  return m ? [m[1]!, m[2]!] : null;
}

/** The element a page marks for an item: `id="item-<id>"` with `tabIndex={-1}`, so it can be scrolled to and focused. */
export const itemDomId = (id: string): string => `item-${id}`;

/** Scroll to the item's element and put focus on it. False when it is not drawn. */
export function openItem(id: string): boolean {
  const el = typeof document === "undefined" ? null : document.getElementById(itemDomId(id));
  if (!el) return false;
  el.scrollIntoView?.({ block: "center" });
  el.focus({ preventScroll: true });
  return true;
}

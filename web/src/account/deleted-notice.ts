// The "Deleted <name>" line the campaign page leaves for My Account (#2879). It is kept in the tab's session storage,
// so it is shown once on the next My Account, and not again after a reload. Storage can be blocked or full, so every
// access is guarded: a blocked store just means the line is not shown.
const KEY = "dmbot-deleted-notice";

export interface DeletedNotice {
  name: string;
  channelsLeft: boolean;
}

export function rememberDeleted(name: string, channelsLeft: boolean): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ name, channelsLeft }));
  } catch {
    // Blocked storage: the notice is simply not shown on My Account.
  }
}

/** Reads the notice once and removes it. Null when there is none, or it cannot be read. */
export function takeDeleted(): DeletedNotice | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    if (!raw) return null;
    const got = JSON.parse(raw) as Partial<DeletedNotice>;
    return typeof got.name === "string" && typeof got.channelsLeft === "boolean"
      ? { name: got.name, channelsLeft: got.channelsLeft }
      : null;
  } catch {
    return null;
  }
}

// The shape of one What's new file (#2468). It lives apart from whats-new.ts, which loads the files,
// so a file can import its type without importing the loader back.
export interface Item {
  /** The day it went live, as YYYY-MM-DD. */
  date: string;
  title: string;
  what: string;
  /** Present while it is deployed but not shown yet: the reason, as `held` has always carried. */
  held?: string;
  /** Breaks a tie on the same day, smallest first. Most files leave it out and come first. */
  order?: number;
}

// The checks behind "Type in my character" (#2832). A pure function, so the rules are tested without the page:
// the page sends only what passes, and the bot checks again on its side.

/** What a player types in, as text, before it is checked. */
export type TypedValues = { className: string; level: string; species: string; names: string };

export const EMPTY_TYPED: TypedValues = { className: "", level: "", species: "", names: "" };

export type TypedCheck =
  | { ok: true; className: string; level: number; species: string; names: string }
  | { ok: false; problem: "class" | "level" };

/** Trims what is typed; a level must be a whole number from 1 to 20 (a "1.5" or "x" is refused). */
export function checkTyped(values: TypedValues): TypedCheck {
  const className = values.className.trim();
  if (className === "") return { ok: false, problem: "class" };
  const level = values.level.trim();
  const n = Number(level);
  if (!/^\d{1,2}$/.test(level) || n < 1 || n > 20) return { ok: false, problem: "level" };
  return { ok: true, className, level: n, species: values.species.trim(), names: values.names.trim() };
}

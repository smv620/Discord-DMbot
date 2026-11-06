/** @jsxImportSource preact */
// The Add a connection form of a line's panel, for a DM (#2323, #3505). Its own file, fetched when a line is opened by a
// DM, so the Story Graph's first download stays light (the account/web.html budget). It holds the "Travels with" pick:
// a companion goes along with a player character or with the party, and the store's rule (#3504) refuses the rest.
import { useState } from "preact/hooks";

import { webPage as words } from "../content/web-words";
import type { WebEdge, WebLinkChange } from "./api";

/** A companion travels with a player character or with the party (#3505, the store's rule #3504). Which end of a pair is the
 * companion: a character who is not a player character, with a player character or a group (the party is a group) at
 * the other end. "a" or "b" for the companion's end, or null when neither order fits, so no choice is offered. The party
 * cannot be told from another group here, so a group is allowed through and the bot's rule refuses the rest. */
export function companionEnd(kindA: string, kindB: string): "a" | "b" | null {
  const companion = (k: string): boolean => k === "npc" || k === "character";
  const target = (k: string): boolean => k === "player_character" || k === "faction";
  if (companion(kindA) && target(kindB)) return "a";
  if (companion(kindB) && target(kindA)) return "b";
  return null;
}

export default function AddLink({
  edge,
  kindOf,
  nameOf,
  act,
}: {
  edge: WebEdge;
  kindOf: (id: string) => string;
  nameOf: (id: string) => string;
  act: (change: WebLinkChange, ok: string, own?: Partial<Record<string, string>>) => Promise<string | null>;
}) {
  const [text, setText] = useState("");
  const [secret, setSecret] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // "Travels with" (#3505): which end is the companion, when the pair can be one at all.
  const companion = companionEnd(kindOf(edge.a), kindOf(edge.b));
  const travels = text.trim().toLowerCase() === words.travelsWithWords;
  return (
    <form
      class="stack"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        if (travels && companion === null) return setProblem(words.travelsWithNo);
        // The companion is the connection's first name: "Cerric travels with Rook", never the other way round.
        const [first, second] = travels && companion === "b" ? [edge.b, edge.a] : [edge.a, edge.b];
        // Only the store's rule saying no (`bad-target`) gets the companion words; every other failure is worded as usual.
        const said = await act(
          { action: "add", a: first, b: second, words: text, secret },
          words.added,
          travels ? { "bad-target": words.travelsWithRefused } : undefined,
        );
        setProblem(said);
        if (said === null) {
          setText("");
          setSecret(false);
        }
      }}
    >
      <h3 class="small">{words.addHeading}</h3>
      <label class="field">
        {words.addWords}
        <input type="text" value={text} maxLength={100} onInput={(e) => setText((e.target as HTMLInputElement).value)} />
      </label>
      {companion !== null && (
        <p class="row">
          <button type="button" class="button secondary" aria-pressed={travels} onClick={() => setText(words.travelsWithWords)}>
            {words.travelsWithPick}
          </button>
          {travels && (
            <span class="small muted">
              {words.travelsWithHint(nameOf(companion === "a" ? edge.a : edge.b), nameOf(companion === "a" ? edge.b : edge.a))}
            </span>
          )}
        </p>
      )}
      <label class="choice">
        <input type="checkbox" checked={secret} onChange={(e) => setSecret((e.target as HTMLInputElement).checked)} />
        <span>{words.addSecret}</span>
      </label>
      <button type="submit" class="button">
        {words.addButton}
      </button>
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
    </form>
  );
}

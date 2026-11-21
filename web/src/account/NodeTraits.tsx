/** @jsxImportSource preact */
// The lineage and class line on a character in the Story Graph's node panel (#3925): one line, DM only, drawn when it arrives. A failed or
// refused read draws nothing, as it is not the panel's main content. Nothing is read while NODE_TRAITS_LIVE is off.
import { useEffect, useState } from "preact/hooks";

import { NODE_TRAITS_LIVE } from "../content/node-traits-switch";
import { nodeTraitsWords as w } from "../content/node-traits-words";
import type { AccountApi, NodeTrait } from "./api";

/** One trait's words: its name, with the 2014 tag and the "not in DMbot's rules" tag where they apply. */
function tagged(t: NodeTrait): string {
  let text = t.name;
  if (t.edition === "2014") text += ` ${w.legacy}`;
  if (!t.known) text += ` ${w.unknown}`;
  return text;
}

/** "🧬 Lineage: Dwarf · Class: Cleric [Legacy 2014], Skald (not in DMbot's rules)": species first, then the classes; "" when there are none. */
export function traitsLine(traits: NodeTrait[]): string {
  const species = traits.filter((t) => t.kind === "species");
  const classes = traits.filter((t) => t.kind === "class");
  const parts: string[] = [];
  if (species.length > 0) parts.push(`${w.lineage} ${species.map(tagged).join(", ")}`);
  if (classes.length > 0) parts.push(`${w.klass} ${classes.map(tagged).join(", ")}`);
  return parts.join(w.separator);
}

export default function NodeTraits({ api, refId, id }: { api: AccountApi; refId: string; id: string }) {
  const [line, setLine] = useState<string | null>(null);

  useEffect(() => {
    if (!NODE_TRAITS_LIVE || !api.nodeTraits) return;
    let stale = false;
    setLine(null);
    api.nodeTraits(refId, id).then(
      (got) => {
        if (!stale) setLine(traitsLine(got) || null);
      },
      () => {
        // A failed or refused read draws nothing.
      },
    );
    return () => {
      stale = true;
    };
  }, [api, refId, id]);

  if (!NODE_TRAITS_LIVE || !line) return null;
  return <p class="small">{line}</p>;
}

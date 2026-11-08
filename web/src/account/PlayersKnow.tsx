/** @jsxImportSource preact */
// The line the players are told about a name waiting to be checked (#3833, website half of the Discord card's "Players know:"),
// so the DM sees what confirming the name also confirms. Shown whole, as written, never cut; nothing at all (no empty box) when
// DMbot made no line, or while its switch is off. The door only sends it to a DM of the campaign. "Change it" opens the name on
// the Names page, where the line has its own box.
import { PLAYERS_LINE_LIVE } from "../content/players-line-switch";
import { playersKnowWords as w } from "../content/players-line-words";

export default function PlayersKnow({ line, campaignRef, name }: { line?: string | null | undefined; campaignRef: string; name: string }) {
  const text = line?.trim();
  if (!PLAYERS_LINE_LIVE || !text) return null;
  return (
    <p class="small players-know">
      <strong>{w.label}</strong> {text}{" "}
      <a href={`/account/names/${encodeURIComponent(campaignRef)}#said=${encodeURIComponent(name)}`} aria-label={w.changeFor(name)}>
        {w.change}
      </a>
    </p>
  );
}

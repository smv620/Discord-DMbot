/** @jsxImportSource preact */
// A campaign's facts and tags (#2460, #2461): 🟢 Listening now, paused, your part in words, and
// sessions, hours played, players, NPCs, started and last played, each left out when there is none.
// Shared by the card on My Account and the heading of the campaign's own page, so they never differ.
import { text } from "../content/account";
import { type Campaign } from "./api";

export function facts(c: Campaign): string[] {
  return [
    c.sessions ? text.sessionsFact(c.sessions) : null,
    c.hoursPlayed ? text.hoursFact(c.hoursPlayed) : null,
    c.players ? text.playersFact(c.players) : null,
    c.npcs ? text.npcsFact(c.npcs) : null,
    c.startedAt ? text.startedFact(c.startedAt) : null,
    c.lastPlayedAt ? text.lastPlayed(c.lastPlayedAt) : text.notPlayed,
  ].filter((f): f is string => f !== null);
}

export default function CampaignFacts({ campaign }: { campaign: Campaign }) {
  return (
    <>
      <ul class="facts small muted" aria-label={text.factsLabel(campaign.name)}>
        {facts(campaign).map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
      <p class="tags">
        {campaign.live && (
          <span class="tag active">
            <span aria-hidden="true">🟢</span> {text.listeningNow}
          </span>
        )}
        {campaign.status === "paused" && (
          <span class="tag paused">
            <span aria-hidden="true">⏸️</span> {text.paused}
          </span>
        )}
        <span class="tag">
          <span aria-hidden="true">
            {campaign.role === "owner" ? "👑" : campaign.role === "player" ? "🎲" : "🤝"}
          </span>{" "}
          {campaign.role === "owner"
            ? text.youRunIt
            : campaign.role === "player"
              ? text.youPlay
              : text.youHelp}
        </span>
      </p>
    </>
  );
}

/** @jsxImportSource preact */
// The Questions page (#3438): every question DMbot waits on, for each campaign the person is a game master
// of, with the same words as Discord's "❓ Questions for you" (#3435). It shows each campaign's counts by kind,
// oldest first, and for each kind either the page that answers it or how to answer it in Discord, never a dead
// button. Counts and campaign names only: the cards come from each kind's own page. The counts are the bot's
// (`question_counts`); older than five minutes, the page says it is catching up.
import { useEffect, useState } from "preact/hooks";

import { QUESTIONS_LIVE } from "../content/questions-switch";
import { kindWords, questionsPage as words } from "../content/questions-words";
import { type AccountApi, ApiError, type QuestionCampaign, type QuestionCounts, type QuestionKind } from "./api";
import { campaignHref } from "./campaignLinks";

/** The bot counts every minute or so: five minutes without a count and the numbers are said to be old. */
export const CATCHING_UP_SECONDS = 300;

function href(ref: string, to: "recaps" | "graph" | "names"): string {
  const e = encodeURIComponent(ref);
  return to === "recaps" ? `/account/recaps?campaign=${e}` : to === "graph" ? `/account/web/${e}` : `/account/names/${e}`;
}

/** Oldest question first; a kind with no time keeps its place after those that have one. */
export function oldestFirst(kinds: QuestionKind[]): QuestionKind[] {
  return [...kinds]
    .filter((k) => k.count > 0)
    .sort((a, b) => (a.oldest ?? Number.MAX_SAFE_INTEGER) - (b.oldest ?? Number.MAX_SAFE_INTEGER));
}

function Campaign({ campaign, serverNow }: { campaign: QuestionCampaign; serverNow: number }) {
  const kinds = oldestFirst(campaign.kinds);
  const old = campaign.countedAt !== null && serverNow - campaign.countedAt > CATCHING_UP_SECONDS;
  return (
    <section class="panel stack" aria-labelledby={`q-${campaign.ref}`}>
      <h2 id={`q-${campaign.ref}`}>
        <a href={campaignHref(campaign.ref)}>{campaign.name}</a>
      </h2>
      {old && <p class="small muted" role="status">{words.catchingUp}</p>}
      {campaign.total === 0 || kinds.length === 0 ? (
        <p>{words.nothing}</p>
      ) : (
        <>
          <p>{words.campaignLine(campaign.name, campaign.total)}</p>
          <ul class="stack">
            {kinds.map((k) => {
              const w = kindWords(k.kind);
              return (
                <li key={k.kind} class="stack">
                  <strong>{words.kindLine(w.label, k.count)}</strong>
                  {w.how === "link" && w.to ? (
                    <a class="small" href={href(campaign.ref, w.to)}>
                      {w.link}
                    </a>
                  ) : (
                    <span class="small muted">{words.discord}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

export default function Questions({ api }: { api: AccountApi }) {
  const [data, setData] = useState<QuestionCounts | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!QUESTIONS_LIVE || !api.myQuestions) return;
    let live = true;
    api
      .myQuestions()
      .then((got) => live && setData(got))
      .catch((error) => {
        // A signed-out visitor is sent to sign in by the page around this one; anything else is a failed load.
        if (live && !(error instanceof ApiError && error.kind === "signed-out")) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [api]);

  if (!QUESTIONS_LIVE || !api.myQuestions) return null;
  if (failed)
    return (
      <p class="warn" role="alert">
        {words.failed}
      </p>
    );
  if (data === null)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (data.campaigns.length === 0) return <p>{words.none}</p>;
  return (
    <div class="stack">
      <p class="muted">{words.intro}</p>
      {data.campaigns.map((c) => (
        <Campaign key={c.ref} campaign={c} serverNow={data.serverNow} />
      ))}
      <p class="small muted">{words.oldestFirst}</p>
    </div>
  );
}

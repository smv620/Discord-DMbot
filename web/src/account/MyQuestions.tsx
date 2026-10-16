/** @jsxImportSource preact */
// "Your questions" (#1373): under the Support form, what the person has sent the team and where
// each stands (Waiting, Answered by DMbot's helper or the team, With the team, Closed) and the first
// line of the latest answer (#1432). Never the person's own words: those stay with the team. With
// none, or with an API that has no list yet, it shows nothing at all (no empty box).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/account";
import { formatDate } from "../content/whats-new";
import { type AccountApi, type MySupport, type SupportQuestion } from "./api";

/** "15 October 2026": the site's fixed words (as on What's new), in UTC, so it reads the same on
 * every computer. */
const day = (seconds: number): string =>
  formatDate(new Date(seconds * 1000).toISOString().slice(0, 10));

function statusWords(question: SupportQuestion, reply: boolean, canWrite: boolean): string {
  if (question.status === "escalated") return text.questionStatus.escalated;
  if (question.status === "answered") {
    const by = question.answeredBy;
    const words = text.questionStatus;
    if (by === "helper") return reply ? words.answeredHelper : words.answeredHelperNoEmail;
    if (by === "team") return reply ? words.answeredTeam : words.answeredTeamNoEmail;
    return reply ? words.answered : words.answeredNoEmail;
  }
  if (question.status === "closed") {
    return canWrite ? text.questionStatus.closedWriteAgain : text.questionStatus.closed;
  }
  return text.questionStatus.new;
}

interface Props {
  api: AccountApi;
  /** Changes when a new question has just been sent: the list is read again. */
  version?: number;
  /** The Support form is on the page (a paid plan): "Closed" can say they may write again. */
  canWrite?: boolean;
}

export function MyQuestions({ api, version = 0, canWrite = false }: Props) {
  const [list, setList] = useState<MySupport | null>(null);

  useEffect(() => {
    if (!api.mySupport) return;
    let current = true;
    api
      .mySupport()
      // A list that can't be read is no list: the form above still works.
      .then((found) => current && setList(found))
      .catch(() => current && setList(null));
    return () => {
      current = false;
    };
  }, [api, version]);

  if (!list || list.tickets.length === 0) return null;
  return (
    <section class="panel" aria-labelledby="questions-heading">
      <h2 id="questions-heading">{text.questionsHeading}</h2>
      <p>{list.reply ? text.questionsLead : text.questionsLeadNoEmail}</p>
      <ul class="questions">
        {list.tickets.map((question, i) => (
          <li key={`${question.at}-${i}`} data-status={question.status}>
            <strong>{question.subject}</strong>
            <span class="small muted">
              {text.questionSent(day(question.at))} · {statusWords(question, list.reply, canWrite)}
            </span>
            {question.latest && (
              <span class="small">{text.questionLatest(question.latest)}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** @jsxImportSource preact */
// "Your questions" (#1373): under the Support form, what the person has sent the team and where
// each stands (Waiting, Answered by DMbot's helper or the team, With the team, Closed) and the first
// line of the latest answer (#1432). Each question opens to its whole conversation (#2193): the
// person's own words, DMbot's helper's answers, their emailed replies and the team's, read only on
// the person's own question. With none, or with an API that has no list yet, it shows nothing at
// all (no empty box).
import { useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/account";
import { formatDate } from "../content/whats-new";
import { type AccountApi, type MySupport, type SupportMessage, type SupportQuestion } from "./api";

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

type Loaded = SupportMessage[] | "loading" | "failed";

/** The conversation under one question: closed until asked for, read only then, and read again
 * each time it is opened so a new reply shows. The words come back as text, never as markup. */
function Conversation({
  api,
  question,
  reply,
  index,
}: {
  api: AccountApi;
  question: SupportQuestion;
  reply: boolean;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // Only the newest read may set the state, so a slow older one can't overwrite it.
  const latest = useRef(0);
  const read = api.supportThread;
  if (!read) return null;
  const id = `conversation-${question.at}-${index}`;

  const load = () => {
    const mine = ++latest.current;
    setLoaded("loading");
    read
      .call(api, question.at)
      .then((found) => mine === latest.current && setLoaded(found))
      .catch(() => mine === latest.current && setLoaded("failed"));
  };

  const toggle = () => {
    if (open) {
      latest.current++;
      setOpen(false);
      return;
    }
    setOpen(true);
    load();
  };

  const parts = Array.isArray(loaded) ? loaded : null;
  // The status line is always on the page, so a screen reader hears the changes in it.
  const status =
    loaded === "loading" ? text.conversationLoading : parts ? text.conversationLoaded : "";
  return (
    <>
      <button
        type="button"
        class="link"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${open ? text.conversationClose : text.conversationOpen}: ${question.subject}`}
        onClick={toggle}
      >
        {open ? text.conversationClose : text.conversationOpen}
      </button>
      <p class="small muted" role="status">
        {open ? status : ""}
      </p>
      <div id={id} role="group" aria-label={text.conversationLabel(question.subject)} hidden={!open}>
        {open && loaded === "failed" && (
          <p class="small" role="alert">
            {text.conversationError}{" "}
            <button type="button" class="link" onClick={load}>
              {text.conversationRetry}
            </button>
          </p>
        )}
        {open && parts && (
          <>
            <p class="small muted">{text.conversationOrder}</p>
            <ol class="conversation">
              {parts.map((part, i) => (
                <li key={`${part.at}-${i}`} data-who={part.who}>
                  <strong>{text.conversationWho[part.who]}</strong>
                  <span class="small muted">{day(part.at)}</span>
                  <span class="conversation-text">{part.text}</span>
                </li>
              ))}
            </ol>
            {parts.length <= 1 && (
              <p class="small muted">{reply ? text.conversationNoReply : text.conversationNoReplyNoEmail}</p>
            )}
          </>
        )}
      </div>
    </>
  );
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
      <h3 id="questions-heading">{text.questionsHeading}</h3>
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
            <Conversation api={api} question={question} reply={list.reply} index={i} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** @jsxImportSource preact */
// The link to the Questions page with a count of what waits (#3438), shown to a game master on My Account and on
// each campaign's page. It shows nothing while the switch is off, for anyone with nothing waiting, and when the
// count can't be read (no badge is better than a wrong one).
import { useEffect, useState } from "preact/hooks";

import { QUESTIONS_LIVE } from "../content/questions-switch";
import { questionsPage as words } from "../content/questions-words";
import type { AccountApi } from "./api";

export default function QuestionsLink({ api, show = true }: { api: AccountApi; show?: boolean }) {
  const [total, setTotal] = useState(0);
  useEffect(() => {
    if (!QUESTIONS_LIVE || !show || !api.questionsTotal) {
      setTotal(0); // a badge from an earlier look must not outlive its reason
      return;
    }
    let live = true;
    api
      .questionsTotal()
      .then((got) => live && setTotal(got.total))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [api, show]);
  if (!QUESTIONS_LIVE || total <= 0) return null;
  return (
    <a class="small" href="/account/questions" aria-label={`${words.linkLabel}: ${words.badge(total)}`}>
      {words.linkLabel} ({total})
    </a>
  );
}

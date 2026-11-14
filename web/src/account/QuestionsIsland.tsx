/** @jsxImportSource preact */
// Picks the API for the Questions page (#3438), as NamesIsland does.
import { useEffect, useState } from "preact/hooks";

import { questionsPage as words } from "../content/questions-words";
import { QUESTIONS_LIVE } from "../content/questions-switch";
import { httpApi, type AccountApi } from "./api";
import Questions from "./Questions";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function QuestionsIsland() {
  const [api, setApi] = useState<AccountApi | null>(null);

  useEffect(() => {
    if (!QUESTIONS_LIVE) return; // switched off: no API is built and nothing is asked
    if (base !== "mock") {
      setApi(httpApi(base));
      return;
    }
    void import("./mock").then(({ mockApi }) => setApi(mockApi("table")));
  }, []);

  if (!QUESTIONS_LIVE) return <p class="muted">{words.notYet}</p>;
  if (!api)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  return (
    <>
      <SignedInLine api={api} />
      <Questions api={api} />
    </>
  );
}

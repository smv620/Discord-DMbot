/** @jsxImportSource preact */
// The part of My Account that writes to the team: Support (or the public Q&A), the person's own questions
// and the "use what I say to improve DMbot" switch. It is below the first view, so My Account loads it
// after the page itself (LazyPanel, #2743) to keep the account page under its weight budget.
import { useState } from "preact/hooks";

import type { AccountApi, Me } from "./api";
import { Improvement } from "./Improvement";
import { MyQuestions } from "./MyQuestions";
import { PublicQuestions, Support } from "./Support";

/** Every account with a plan (Try It too, #1435) writes to the team in private; someone with no
 * account yet is pointed to the public Q&A.
 * An API older than #1340 has no canSupport, so it shows the Q&A (#1340). */
export default function AccountTalk({ api, siteKey, me }: { api: AccountApi; siteKey: string; me: Me }) {
  // Bumped when a question is sent, so the list below the form reads again (#1373).
  const [sentCount, setSentCount] = useState(0);
  return (
    <>
      {me.canSupport === true && api.sendSupport ? (
        <Support
          api={api}
          siteKey={siteKey}
          tryIt={me.plan?.id === "try-it"}
          onSent={() => setSentCount((n) => n + 1)} />
      ) : (
        <PublicQuestions />
      )}
      <MyQuestions api={api} version={sentCount} canWrite={me.canSupport === true && !!api.sendSupport} />
      <Improvement api={api} />
    </>
  );
}

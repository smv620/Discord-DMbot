/** @jsxImportSource preact */
// What opens under a kind on the Questions page when it is answered on the page (#3438): the same panels as the
// rest of the site, each loaded only when shown. New names to check are one list for each recap (`QuestionsNames`);
// connections waiting, "Which one?" and facts to check are the Story Graph's one waiting list; what people believe
// is the Story Graph's readings list. Every one is campaign-wide and answers, waits and undoes as it does on its
// own page, so nothing here is a second copy. `onLeft` tells the walk how many items the panel still has (#4740).
import { useEffect, useState } from "preact/hooks";

import { HAND_OVER_LIVE } from "../content/hand-over-switch";
import { type AccountApi } from "./api";
import LazyPanel from "./LazyPanel";
import QuestionsNames from "./QuestionsNames";

export type HereGroup = "names" | "waiting" | "readings" | "traits" | "secrets" | "joins" | "leaves" | "reveals" | "heard" | "deeds" | "lookalikes";

/** The panel a kind opens, or null when it is not answered on this page. Kinds in one group share one panel. */
export function groupOf(kind: string): HereGroup | null {
  if (kind === "names") return "names";
  if (kind === "connections" || kind === "which" || kind === "family" || kind === "claims") return "waiting";
  if (kind === "readings") return "readings";
  if (kind === "traits") return "traits";
  if (kind === "secrets") return "secrets";
  if (kind === "party_joins") return "joins";
  if (kind === "party_leaves") return "leaves";
  if (kind === "deeds") return "deeds";
  if (kind === "reveals") return "reveals";
  if (kind === "heard") return "heard";
  if (kind === "lookalikes") return "lookalikes";
  return null;
}

export default function QuestionsHere({
  api,
  campaignRef,
  group,
  id,
  onLeft,
  onKinds,
  waiting,
}: {
  api: AccountApi;
  campaignRef: string;
  group: HereGroup;
  id: string;
  onLeft?: (left: number) => void;
  /** What the waiting list holds of each kind now (#5356); only that group says. */
  onKinds?: (kinds: { claims: number; family: number; which: number; connections: number }) => void;
  waiting?: number;
}) {
  // The waiting group has two panels; each reports apart, and the group tells the sum once both mounted panels have (#4740).
  const [waitingLeft, setWaitingLeft] = useState<number | null>(null);
  const [handLeft, setHandLeft] = useState<number | null>(null);
  const handMounted = HAND_OVER_LIVE && api.handOverQuestions !== undefined;
  const report = onLeft ?? ((): void => undefined); // (a panel always has something to call)
  useEffect(() => {
    if (group !== "waiting" || !onLeft || waitingLeft === null) return;
    if (handMounted && handLeft === null) return;
    onLeft(waitingLeft + (handMounted ? (handLeft ?? 0) : 0));
  }, [group, waitingLeft, handLeft, handMounted]);

  if (group === "names") return <QuestionsNames api={api} campaignRef={campaignRef} id={id} onLeft={report} waiting={waiting ?? 0} />;
  if (group === "traits")
    return (
      <LazyPanel load={() => import("./TraitQuestions")} what="the lineage and class questions" props={{ api, campaignRef, id, onLeft: report }} />
    );
  if (group === "joins")
    return <LazyPanel load={() => import("./JoinQuestions")} what="the join question" props={{ api, campaignRef, id, onLeft: report }} />;
  if (group === "reveals")
    return <LazyPanel load={() => import("./RevealQuestions")} what="things the characters learned" props={{ api, campaignRef, id, onLeft: report }} />;
  if (group === "deeds")
    return <LazyPanel load={() => import("./DeedQuestions")} what="the deeds to record" props={{ api, campaignRef, id, onLeft: report }} />;
  if (group === "leaves")
    return <LazyPanel load={() => import("./LeaveQuestions")} what="the still in the party questions" props={{ api, campaignRef, id, onLeft: report }} />;
  if (group === "heard")
    return <LazyPanel load={() => import("./HeardQuestions")} what="the Did they mean questions" props={{ api, campaignRef, id, onLeft: report }} />;
  if (group === "lookalikes")
    return <LazyPanel load={() => import("./LookAlikes")} what="the look-alike names" props={{ api, campaignRef, id, onLeft: report, heading: false }} />;
  if (group === "secrets")
    return (
      <LazyPanel load={() => import("./SecretQuestions")} what="the known or secret questions" props={{ api, campaignRef, id, onLeft: report }} />
    );
  return (
    <div id={id} class="stack">
      {group === "waiting" ? (
        <>
          <LazyPanel load={() => import("./WebWaiting")} what="the waiting connections" props={{ api, campaignRef, onLeft: setWaitingLeft, ...(onKinds ? { onKinds } : {}) }} />
          {handMounted && (
            <LazyPanel
              load={() => import("./HandOverQuestions")}
              what="the items that changed hands"
              props={{ api, campaignRef, id: `${id}-handover`, onLeft: setHandLeft }}
            />
          )}
        </>
      ) : (
        <LazyPanel load={() => import("./WebReadings")} what="what people believe" props={{ api, campaignRef, onLeft: report }} />
      )}
    </div>
  );
}

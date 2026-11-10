/** @jsxImportSource preact */
// What opens under a kind on the Questions page when it is answered on the page (#3438): the same panels as the
// rest of the site, each loaded only when shown. New names to check are one list for each recap (`QuestionsNames`);
// connections waiting, "Which one?" and facts to check are the Story Graph's one waiting list; what people believe
// is the Story Graph's readings list. Every one is campaign-wide and answers, waits and undoes as it does on its
// own page, so nothing here is a second copy.
import { type AccountApi } from "./api";
import LazyPanel from "./LazyPanel";
import QuestionsNames from "./QuestionsNames";

export type HereGroup = "names" | "waiting" | "readings" | "traits";

/** The panel a kind opens, or null when it is not answered on this page. Kinds in one group share one panel. */
export function groupOf(kind: string): HereGroup | null {
  if (kind === "names") return "names";
  if (kind === "connections" || kind === "which" || kind === "family" || kind === "claims") return "waiting";
  if (kind === "readings") return "readings";
  if (kind === "traits") return "traits";
  return null;
}

export default function QuestionsHere({ api, campaignRef, group, id }: { api: AccountApi; campaignRef: string; group: HereGroup; id: string }) {
  if (group === "names") return <QuestionsNames api={api} campaignRef={campaignRef} id={id} />;
  if (group === "traits") return <LazyPanel load={() => import("./TraitQuestions")} what="the lineage and class questions" props={{ api, campaignRef, id }} />;
  return (
    <div id={id} class="stack">
      {group === "waiting" ? (
        <LazyPanel load={() => import("./WebWaiting")} what="the waiting connections" props={{ api, campaignRef }} />
      ) : (
        <LazyPanel load={() => import("./WebReadings")} what="what people believe" props={{ api, campaignRef }} />
      )}
    </div>
  );
}

/** @jsxImportSource preact */
// Picks the API for the hand-over accept page, as NamesIsland does for the Names page (#2534).
import { useEffect, useState } from "preact/hooks";

import { handoverWords as words } from "../content/handover-words";
import { httpApi, type AccountApi } from "./api";
import Handover from "./Handover";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function HandoverIsland() {
  const [api, setApi] = useState<AccountApi | null>(null);

  useEffect(() => {
    if (base !== "mock") {
      setApi(httpApi(base));
      return;
    }
    void import("./mock").then(({ mockApi }) => setApi(mockApi("table")));
  }, []);

  if (!api)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  return <Handover api={api} hash={window.location.hash} />;
}

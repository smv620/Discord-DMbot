/** @jsxImportSource preact */
// Picks the API for the Story timeline page, as HouseRulesIsland does for the house-rules pages (#2462).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/timeline";
import { httpApi, type AccountApi } from "./api";
import SignedInLine from "./SignedInLine";
import TimelinePage from "./Timeline";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function TimelineIsland() {
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
        {text.loading}
      </p>
    );
  return (
    <>
      <SignedInLine api={api} />
      <TimelinePage api={api} path={window.location.pathname} />
    </>
  );
}

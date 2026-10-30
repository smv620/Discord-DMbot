/** @jsxImportSource preact */
// Picks the API for the Recap style page, as LoreIsland does for the Upload lore page (#2985).
import { useEffect, useState } from "preact/hooks";

import { recapStylePage as words } from "../content/recap-style-words";
import { httpApi, type AccountApi } from "./api";
import RecapStyleView from "./RecapStyle";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function RecapStyleIsland() {
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
  return (
    <>
      <SignedInLine api={api} />
      <RecapStyleView api={api} path={window.location.pathname} />
    </>
  );
}

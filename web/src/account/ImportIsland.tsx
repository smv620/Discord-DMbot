/** @jsxImportSource preact */
// Picks the API for the Import sessions page, as TranscriptsIsland does for the transcript pages (#2107).
import { useEffect, useState } from "preact/hooks";

import { importPage as words } from "../content/import-words";
import { httpApi, type AccountApi } from "./api";
import Import from "./Import";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function ImportIsland() {
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
  return <Import api={api} path={window.location.pathname} />;
}

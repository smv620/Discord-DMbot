/** @jsxImportSource preact */
// Picks the API for the shared recap page, as the other islands do (#2119).
import { useEffect, useState } from "preact/hooks";

import { publicWords as words } from "../content/recap-share";
import { httpApi, type AccountApi } from "./api";
import PublicRecapPage from "./PublicRecap";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function PublicRecapIsland() {
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
  return <PublicRecapPage api={api} path={window.location.pathname} />;
}

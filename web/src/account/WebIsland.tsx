/** @jsxImportSource preact */
// Picks the API for the Web page, as NamesIsland does for the Names page (#2324).
import { useEffect, useState } from "preact/hooks";

import { webPage as words } from "../content/web-words";
import { httpApi, type AccountApi } from "./api";
import Web from "./Web";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function WebIsland() {
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
      <Web api={api} path={window.location.pathname} />
    </>
  );
}

/** @jsxImportSource preact */
// Picks the API for the Names page, as ImportIsland does for the Import page (#2295).
import { useEffect, useState } from "preact/hooks";

import { namesPage as words } from "../content/names-words";
import { httpApi, type AccountApi } from "./api";
import Names from "./Names";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function NamesIsland() {
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
      <Names api={api} path={window.location.pathname} />
    </>
  );
}

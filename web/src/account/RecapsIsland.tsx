/** @jsxImportSource preact */
// Picks the API for the recap pages, as AccountIsland does for /account (#1386).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/recaps";
import { httpApi, type AccountApi } from "./api";
import SignedInLine from "./SignedInLine";
import Recaps from "./Recaps";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function RecapsIsland() {
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
      <Recaps api={api} path={window.location.pathname} />
    </>
  );
}

/** @jsxImportSource preact */
// Picks the API for the Load a backup page, as SharingIsland does for the Shared rules page (#2745).
import { useEffect, useState } from "preact/hooks";

import { restoreWords } from "../content/restore-words";
import { httpApi, type AccountApi } from "./api";
import Restore from "./Restore";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function RestoreIsland() {
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
        {restoreWords.loading}
      </p>
    );
  return (
    <>
      <SignedInLine api={api} />
      <Restore api={api} />
    </>
  );
}

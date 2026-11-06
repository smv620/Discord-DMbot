/** @jsxImportSource preact */
// Picks the API for the Upload lore page, as ImportIsland does for the Import page (#3022).
import { useEffect, useState } from "preact/hooks";

import { lorePage as words } from "../content/lore-words";
import { httpApi, type AccountApi } from "./api";
import Lore from "./Lore";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function LoreIsland() {
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
      <Lore api={api} path={window.location.pathname} />
    </>
  );
}

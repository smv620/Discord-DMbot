/** @jsxImportSource preact */
// Picks the API for the Npc page, as ImportIsland does for the Import page (#2266).
import { useEffect, useState } from "preact/hooks";

import { npcPage as words } from "../content/npc-words";
import { httpApi, type AccountApi } from "./api";
import Npc from "./Npc";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function NpcIsland() {
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
      <Npc api={api} path={window.location.pathname} />
    </>
  );
}

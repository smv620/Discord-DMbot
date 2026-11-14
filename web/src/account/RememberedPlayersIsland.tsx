/** @jsxImportSource preact */
// Picks the API for the People you play with page, as PicturesIsland does (#4226).
import { useEffect, useState } from "preact/hooks";

import { rememberedWords as words } from "../content/remembered-players-words";
import { httpApi, type AccountApi } from "./api";
import { httpPlayers, type PlayersApi } from "./players-api";
import RememberedPlayers from "./RememberedPlayers";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function RememberedPlayersIsland() {
  const [apis, setApis] = useState<{ account: AccountApi; players: PlayersApi } | null>(null);

  useEffect(() => {
    if (base !== "mock") {
      setApis({ account: httpApi(base), players: httpPlayers(base) });
      return;
    }
    void Promise.all([import("./mock"), import("./players-api")]).then(([m, p]) => setApis({ account: m.mockApi("table"), players: p.mockPlayers() }));
  }, []);

  if (!apis)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  return (
    <>
      <SignedInLine api={apis.account} />
      <RememberedPlayers api={apis.players} />
    </>
  );
}

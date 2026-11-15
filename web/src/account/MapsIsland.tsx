/** @jsxImportSource preact */
// Picks the API for the Maps page, as PicturesIsland does for the Pictures page (#4347).
import { useEffect, useState } from "preact/hooks";

import { mapsWords as words } from "../content/maps-words";
import { httpApi, type AccountApi } from "./api";
import Maps, { refFromPath } from "./Maps";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function MapsIsland() {
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
      <Maps api={api} refId={refFromPath(window.location.pathname) ?? ""} />
    </>
  );
}

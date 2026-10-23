/** @jsxImportSource preact */
// Picks the API for the Shared rules page, as HouseRulesIsland does for the house-rules pages (#2460).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/sharing";
import { httpApi, type AccountApi } from "./api";
import SignedInLine from "./SignedInLine";
import SharingPage from "./Sharing";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function SharingIsland() {
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
      <SharingPage api={api} />
    </>
  );
}

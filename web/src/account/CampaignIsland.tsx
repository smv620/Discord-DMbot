/** @jsxImportSource preact */
// Picks the API for the campaign page, as ImportIsland does for the Import page (#2416).
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { httpApi, type AccountApi } from "./api";
import CampaignPageView from "./CampaignPage";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function CampaignIsland() {
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
      <CampaignPageView api={api} path={window.location.pathname} />
    </>
  );
}

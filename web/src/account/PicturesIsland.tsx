/** @jsxImportSource preact */
// Picks the API for the Pictures page, as TimelineIsland does for the timeline (#3276).
import { useEffect, useState } from "preact/hooks";

import { picturesPage as words } from "../content/pictures-words";
import { httpApi, type AccountApi } from "./api";
import Pictures from "./Pictures";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function PicturesIsland() {
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
      <Pictures api={api} path={window.location.pathname} />
    </>
  );
}

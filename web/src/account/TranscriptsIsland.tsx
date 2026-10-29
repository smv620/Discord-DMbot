/** @jsxImportSource preact */
// Picks the API for the transcript pages, as RecapsIsland does for the recap pages (#1501).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/transcripts";
import { httpApi, type AccountApi } from "./api";
import SignedInLine from "./SignedInLine";
import Transcripts from "./Transcripts";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function TranscriptsIsland() {
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
      <Transcripts api={api} path={window.location.pathname} />
    </>
  );
}

/** @jsxImportSource preact */
// Picks the API for the page of one name to check (#3604), as QuestionsIsland does. The campaign and the name come from
// the address (?campaign=…&name=…), which Discord's "More options…" button fills in. Switched off: no API is built
// and nothing is asked.
import { useEffect, useState } from "preact/hooks";

import { NAME_CLARIFY_LIVE } from "../content/name-clarify-switch";
import { clarifyWords as words } from "../content/name-clarify-words";
import { httpApi, type AccountApi } from "./api";
import Clarify from "./Clarify";
import SignedInLine from "./SignedInLine";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function ClarifyIsland() {
  const [api, setApi] = useState<AccountApi | null>(null);
  const query = typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const campaign = query.get("campaign") ?? "";
  const name = query.get("name") ?? "";

  useEffect(() => {
    if (!NAME_CLARIFY_LIVE) return;
    if (base !== "mock") {
      setApi(httpApi(base));
      return;
    }
    void import("./mock").then(({ mockApi }) => setApi(mockApi("table")));
  }, []);

  if (!NAME_CLARIFY_LIVE) return <p class="muted">{words.notYet}</p>;
  if (!api)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (campaign === "" || name === "") return <p class="muted">{words.missing}</p>;
  return (
    <>
      <SignedInLine api={api} />
      <Clarify key={`${campaign}:${name}`} api={api} refId={campaign} nameId={name} />
    </>
  );
}

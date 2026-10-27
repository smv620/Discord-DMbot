/** @jsxImportSource preact */
// Picks the API for the homebrew pages, as RecapsIsland does for the recap pages (#1521).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/homebrew";
import { httpApi, type AccountApi } from "./api";
import SignedInLine from "./SignedInLine";
import HomebrewPage from "./Homebrew";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function HomebrewIsland() {
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
      <HomebrewPage api={api} path={window.location.pathname} />
    </>
  );
}

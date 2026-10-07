/** @jsxImportSource preact */
// Picks the API for the account page. PUBLIC_API_BASE is set at build time: the web API's
// address in production, or "mock" to preview every state without core (?demo=table etc.).
import { useEffect, useState } from "preact/hooks";

import { text } from "../content/account";
import Account from "./Account";
import { httpApi, type AccountApi } from "./api";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

export default function AccountIsland() {
  const [api, setApi] = useState<AccountApi | null>(null);
  const search = typeof window === "undefined" ? "" : window.location.search;

  useEffect(() => {
    if (base !== "mock") {
      setApi(httpApi(base));
      return;
    }
    // Only in a mock build: the pretend API is a separate chunk, never loaded otherwise.
    void import("./mock").then(({ mockApi }) => {
      const demo = new URLSearchParams(search).get("demo") ?? "table";
      setApi(mockApi(demo as Parameters<typeof mockApi>[0]));
    });
  }, [search]);

  if (!api) return <p class="muted" role="status">{text.loading}</p>;
  return <Account api={api} search={search} />;
}

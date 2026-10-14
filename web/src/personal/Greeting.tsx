/** @jsxImportSource preact */
// The greeting at the top of /pricing and /hello for someone who came from a personal link
// (#1231). Without ?uid=&t= it makes no request and shows nothing. The name is never in the
// address: only the signed code is, and the name comes back from the API.
import { useEffect, useState } from "preact/hooks";

import { personal } from "../content/personal";
import { fetchPersonal, type Personal } from "./api";

/** Take `uid` and `t` out of the address bar once the page has used them, so a copied address or a
 * screenshot can't greet someone else by name for the next 30 days. The path, the hash and any
 * other part of the query stay. */
export function forgetPersonalLink(): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.delete("uid");
  url.searchParams.delete("t");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

export function Greeting({
  search,
  base,
  fetcher,
}: {
  search: string;
  base: string;
  fetcher?: typeof fetch;
}) {
  const [found, setFound] = useState<Personal | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(search);
    const uid = params.get("uid");
    const token = params.get("t");
    if (!uid || !token || base === "mock") return;
    let live = true;
    void fetchPersonal(base, uid, token, fetcher).then((who) => {
      if (!live) return;
      setFound(who);
      forgetPersonalLink();
    });
    return () => {
      live = false;
    };
  }, [search, base, fetcher]);
  if (found === null) return null;
  return (
    <div class="personal" role="status">
      {found.name !== null && <p class="lead">{personal.hello(found.name)}</p>}
      <p>{personal.points(found.experiencePoints)}</p>
      <p class="muted small">{personal.pointsNote}</p>
    </div>
  );
}

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

/** The island the pages mount: browser only, so the address bar is there to read. */
export default function GreetingIsland() {
  return <Greeting search={window.location.search} base={base} />;
}

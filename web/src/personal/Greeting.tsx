/** @jsxImportSource preact */
// The greeting at the top of /pricing and /hello for someone who came from a personal link
// (#1231). Without ?uid=&t= it makes no request and shows nothing. The name is never in the
// address: only the signed code is, and the name comes back from the API.
import { useEffect, useState } from "preact/hooks";

import { personal } from "../content/personal";
import { fetchPersonal, type Personal } from "./api";

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
    void fetchPersonal(base, uid, token, fetcher).then((who) => live && setFound(who));
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

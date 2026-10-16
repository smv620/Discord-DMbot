/** @jsxImportSource preact */
// The unsubscribe link in an offer email opens /unsubscribe?uid=&t= (#1229, #1231). The signed
// code in the link is the proof, so no sign-in is needed. The call is made by this script, not by
// loading the page, so a mail program that only fetches the page can't unsubscribe anyone.
import { useEffect, useState } from "preact/hooks";

import { unsubscribe } from "../content/unsubscribe";
import { forgetPersonalLink } from "../personal/Greeting";

type State = "working" | "done" | "bad";

export async function stopEmails(
  base: string,
  uid: string,
  token: string,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  try {
    const response = await fetcher(
      `${base.replace(/\/+$/, "")}/email/unsubscribe`,
      {
        method: "POST",
        credentials: "omit",
        headers: { "X-DMbot-Request": "1", "Content-Type": "application/json" },
        body: JSON.stringify({ uid, t: token }),
      },
    );
    return response.ok;
  } catch {
    return false;
  }
}

export function Unsubscribe({
  search,
  base,
  fetcher,
}: {
  search: string;
  base: string;
  fetcher?: typeof fetch;
}) {
  const [state, setState] = useState<State>("working");
  useEffect(() => {
    const params = new URLSearchParams(search);
    const uid = params.get("uid");
    const token = params.get("t");
    if (!uid || !token || base === "mock") {
      setState("bad");
      return;
    }
    let live = true;
    void stopEmails(base, uid, token, fetcher).then((ok) => {
      if (!live) return;
      setState(ok ? "done" : "bad");
      forgetPersonalLink(); // the code has been used; it needn't stay in the address bar
    });
    return () => {
      live = false;
    };
  }, [search, base, fetcher]);
  return (
    <p
      role="status"
      class={state === "bad" ? "warn" : state === "done" ? "ok" : "muted"}
    >
      {state === "working"
        ? unsubscribe.working
        : state === "done"
          ? unsubscribe.done
          : unsubscribe.bad}
    </p>
  );
}

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";

/** The island the page mounts: browser only, so the address bar is there to read. */
export default function UnsubscribeIsland() {
  return <Unsubscribe search={window.location.search} base={base} />;
}

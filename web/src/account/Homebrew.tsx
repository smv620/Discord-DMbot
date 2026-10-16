/** @jsxImportSource preact */
// The homebrew (#1525): every campaign with an entry the DM let the players know, and one
// campaign's entries to read and download. Anyone in the Discord server may read them, so this is
// for players as much as DMs; the DM adds and changes entries in Discord, never here. The API
// sends only the entries the DM let the players know, so a hidden one is never on this page.
import { useEffect, useRef, useState } from "preact/hooks";

import { kindOrder, problem, text } from "../content/homebrew";
import {
  ApiError,
  type AccountApi,
  type HomebrewCampaign,
  type HomebrewPage,
} from "./api";

/** The campaign in the address: `/account/homebrew/<server>.<campaign>`, or null for the list. */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/homebrew\/([^/]+?)\/?$/.exec(path);
  return match ? decodeURIComponent(match[1] ?? "") : null;
}

type Load<T> =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; value: T };

function useRead<T>(read: (() => Promise<T>) | null, deps: unknown[]): Load<T> {
  const [load, setLoad] = useState<Load<T>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setLoad({ kind: "loading" });
    if (!read) {
      setLoad({ kind: "error", message: text.failed });
      return;
    }
    read().then(
      (value) => live && setLoad({ kind: "ready", value }),
      (error: unknown) => {
        if (!live) return;
        const kind = error instanceof ApiError ? error.kind : "server";
        setLoad(
          kind === "signed-out"
            ? { kind: "signed-out" }
            : { kind: "error", message: problem(kind) },
        );
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return load;
}

function Shell<T>({
  api,
  load,
  children,
}: {
  api: AccountApi;
  load: Load<T>;
  children: (value: T) => preact.ComponentChildren;
}) {
  if (load.kind === "loading")
    return (
      <p class="muted" role="status">
        {text.loading}
      </p>
    );
  if (load.kind === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{text.signedOut}</p>
        <a class="button" href={api.signInUrl()}>
          {text.signIn}
        </a>
      </div>
    );
  if (load.kind === "error")
    return (
      <p class="warn" role="alert">
        {load.message}
      </p>
    );
  return <>{children(load.value)}</>;
}

export default function HomebrewPageView({
  api,
  path,
}: {
  api: AccountApi;
  path: string;
}) {
  const ref = refFromPath(path);
  return (
    <div class="stack">
      <p>
        <a href="/account">{text.account}</a>
      </p>
      {ref === null ? (
        <Campaigns api={api} />
      ) : (
        <Entries api={api} campaignRef={ref} />
      )}
    </div>
  );
}

function Campaigns({ api }: { api: AccountApi }) {
  const load = useRead(
    api.homebrewCampaigns ? () => api.homebrewCampaigns!() : null,
    [api],
  );
  return (
    <Shell api={api} load={load}>
      {(campaigns: HomebrewCampaign[]) =>
        campaigns.length === 0 ? (
          <p class="panel" role="status">
            {text.empty}
          </p>
        ) : (
          <>
            <p class="lead">{text.lead}</p>
            <ul class="campaigns">
              {campaigns.map((c) => (
                <li key={c.ref}>
                  <span class="campaign-name">{c.name}</span>
                  <span class="small">
                    {text.campaignLine(c.serverName, c.entries)}
                  </span>
                  <a
                    href={`/account/homebrew/${encodeURIComponent(c.ref)}`}
                    aria-label={text.openCampaign(c.name)}
                  >
                    {text.open}
                  </a>
                </li>
              ))}
            </ul>
          </>
        )
      }
    </Shell>
  );
}

function Entries({
  api,
  campaignRef,
}: {
  api: AccountApi;
  campaignRef: string;
}) {
  const load = useRead(api.homebrew ? () => api.homebrew!(campaignRef) : null, [
    api,
    campaignRef,
  ]);
  return (
    <Shell api={api} load={load}>
      {(found: HomebrewPage) => {
        // Grouped by kind in the bot's order; the API already lists each kind by name.
        const kinds = kindOrder.filter((k) =>
          found.entries.some((e) => e.kind === k),
        );
        for (const e of found.entries)
          if (!kinds.includes(e.kind)) kinds.push(e.kind);
        return (
          <>
            <p>
              <a href="/account/homebrew">{text.allCampaigns}</a>
            </p>
            <h2>{found.name}</h2>
            <p class="small">{text.leadOneCampaign}</p>
            <p class="small">{text.dmChanges}</p>
            {found.entries.length === 0 ? (
              <p class="panel" role="status">
                {text.empty}
              </p>
            ) : (
              <>
                {kinds.map((kind) => (
                  <section key={kind} aria-label={text.kinds[kind] ?? kind}>
                    <h3>{text.kinds[kind] ?? kind}</h3>
                    <ul class="house-rules homebrew-list">
                      {found.entries
                        .filter((e) => e.kind === kind)
                        .map((e) => (
                          <li key={e.name}>
                            <strong>{e.name}</strong>
                            {e.summary && (
                              <span class="small muted">{e.summary}</span>
                            )}
                            <span class="homebrew-text">{e.text}</span>
                          </li>
                        ))}
                    </ul>
                  </section>
                ))}
                <Download
                  api={api}
                  campaignRef={campaignRef}
                  fileName={found.fileName}
                />
              </>
            )}
          </>
        );
      }}
    </Shell>
  );
}

function Download({
  api,
  campaignRef,
  fileName,
}: {
  api: AccountApi;
  campaignRef: string;
  fileName: string;
}) {
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const busy = useRef(false);
  if (!api.homebrewFile) return null;
  async function save() {
    if (busy.current) return;
    busy.current = true;
    setState("busy");
    try {
      const blob = await api.homebrewFile!(campaignRef);
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setState("idle");
    } catch {
      setState("failed");
    } finally {
      busy.current = false;
    }
  }
  return (
    <p>
      <button
        type="button"
        class="button secondary"
        onClick={() => void save()}
      >
        {state === "busy" ? text.downloading : text.download}
      </button>{" "}
      <span class="small muted">{text.downloadNote}</span>{" "}
      <span role="status" class="warn">
        {state === "failed" ? text.downloadFailed : ""}
      </span>
    </p>
  );
}

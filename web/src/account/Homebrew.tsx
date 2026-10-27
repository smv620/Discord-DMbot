/** @jsxImportSource preact */
// The homebrew (#1525): every campaign with homebrew, and one
// campaign's entries to read and download. Anyone in the Discord server may read them, so this is
// for players as much as DMs; the DM adds and changes entries in Discord, never here. Nothing is
// hidden from players (#1643); an entry the DM turned off shows muted, marked (off).
import { useEffect, useRef, useState } from "preact/hooks";

import { kindOrder, problem, text } from "../content/homebrew";
import { sharingCard } from "../content/sharing";
import {
  ApiError,
  type AccountApi,
  type HomebrewCampaign,
  type HomebrewEntry,
  type HomebrewPage,
} from "./api";
import CampaignNav from "./CampaignNav";

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
  load,
  children,
}: {
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
        {" · "}
        <a href="/account/sharing">{sharingCard.link}</a>
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
    <Shell load={load}>
      {(campaigns: HomebrewCampaign[]) =>
        campaigns.length === 0 ? (
          <p class="panel" role="status">
            {text.empty}
          </p>
        ) : (
          <>
            <p class="lead">{text.lead}</p>
            <p class="small">
              {text.rulesNote} <a href="/account/house-rules">{text.rulesLink}</a>
            </p>
            <ul class="campaigns">
              {campaigns.map((c) => (
                <li key={c.ref}>
                  <span class="campaign-name">{c.name}</span>
                  <span class="small">
                    {text.campaignLine(c.serverName, c.entries, c.packs)}
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

/** Entries grouped by kind in the bot's order (the API already lists each kind by name). Pack
 * lists use plain headings, not landmarks, so the page has one region per kind of its own. */
function EntryGroups({
  entries,
  landmark,
}: {
  entries: HomebrewEntry[];
  landmark: boolean;
}) {
  const kinds = kindOrder.filter((k) => entries.some((e) => e.kind === k));
  for (const e of entries) if (!kinds.includes(e.kind)) kinds.push(e.kind);
  return (
    <>
      {kinds.map((kind) => {
        const body = (
          <>
            <h3>{text.kinds[kind] ?? kind}</h3>
            <ul class="house-rules homebrew-list">
              {entries
                .filter((e) => e.kind === kind)
                .map((e) => (
                  <li key={e.name} class={e.enabled === false ? "muted" : undefined}>
                    <span>
                      <strong>{e.name}</strong>
                      {e.enabled === false && (
                        <>
                          {" "}
                          <span class="muted homebrew-tag">{text.off}</span>
                        </>
                      )}
                      {e.fromBook && (
                        <>
                          {" "}
                          <span class="muted homebrew-tag">{text.fromBook}</span>
                        </>
                      )}
                      {e.legacy && (
                        <>
                          {" "}
                          <span class="muted homebrew-tag">{text.legacy}</span>
                        </>
                      )}
                    </span>
                    {e.source && <span class="small muted">{e.source}</span>}
                    {e.summary && <span class="small muted">{e.summary}</span>}
                    <span class="homebrew-text">{e.text}</span>
                  </li>
                ))}
            </ul>
          </>
        );
        return landmark ? (
          <section key={kind} aria-label={text.kinds[kind] ?? kind}>
            {body}
          </section>
        ) : (
          <div key={kind}>{body}</div>
        );
      })}
    </>
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
    <Shell load={load}>
      {(found: HomebrewPage) => {
        const packs = found.packs ?? []; // an older API has none
        return (
          <>
            <p>
              <a href="/account/homebrew">{text.allCampaigns}</a>
            </p>
            <CampaignNav api={api} campaignRef={campaignRef} page="homebrew" name={found.name} />
            <h2>{found.name}</h2>
            <p class="small">
              {text.rulesNote} <a href="/account/house-rules">{text.rulesLink}</a>
            </p>
            <p class="small">{text.leadOneCampaign}</p>
            <p class="small">{text.dmChanges}</p>
            {(found.entries.some((e) => e.fromBook) ||
              found.entries.some((e) => e.legacy)) && (
              <p class="small">{text.bookNote}</p>
            )}
            {found.entries.length === 0 ? (
              <p class="panel" role="status">
                {packs.length > 0 ? text.emptyButPack : text.empty}
              </p>
            ) : (
              <>
                <EntryGroups entries={found.entries} landmark />
                <Download
                  api={api}
                  campaignRef={campaignRef}
                  fileName={found.fileName}
                  withPacks={packs.length > 0}
                />
              </>
            )}
            {packs.length > 0 && (
              <section aria-labelledby="packs-heading">
                <h3 id="packs-heading">{text.packsHeading}</h3>
                <p class="small">{text.packsNote}</p>
                {packs.map((p) => (
                  <div key={`${p.name}-${p.version}`}>
                    <p>
                      <strong>{text.packLine(p.name, p.version)}</strong>
                    </p>
                    {p.entries.length === 0 ? (
                      <p class="small muted">{text.packNone}</p>
                    ) : (
                      <EntryGroups entries={p.entries} landmark={false} />
                    )}
                  </div>
                ))}
              </section>
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
  withPacks,
}: {
  api: AccountApi;
  campaignRef: string;
  fileName: string;
  withPacks: boolean;
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
      <span class="small muted">
        {withPacks ? text.downloadNotePacks : text.downloadNote}
      </span>{" "}
      <span role="status" class="warn">
        {state === "failed" ? text.downloadFailed : ""}
      </span>
    </p>
  );
}

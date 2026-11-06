/** @jsxImportSource preact */
// The house rules (#1521): every campaign with a house rule or a followed set in the person's
// servers, and one campaign's rules to read and download. Anyone in the Discord server may read
// them (the bot's `/dmbot houserules`), so this is for players as much as DMs; the DM changes the
// rules on the DM screen, never here. The API decides who sees what.
import { useEffect, useRef, useState } from "preact/hooks";

import { problem, text } from "../content/house-rules";
import {
  ApiError,
  type AccountApi,
  type HouseRuleCampaign,
  type HouseRules,
} from "./api";
import { sharingCard } from "../content/sharing";
import { RULE_SHARING_LIVE } from "../content/rule-sharing-switch";
import { HOUSE_FILE_LIVE } from "../content/house-file-switch";
import CampaignNav from "./CampaignNav";
import LazyPanel from "./LazyPanel";

/** The campaign in the address: `/account/house-rules/<server>.<campaign>`, or null for the list. */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/house-rules\/([^/]+?)\/?$/.exec(path);
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

export default function HouseRulesPage({ api, path }: { api: AccountApi; path: string }) {
  const ref = refFromPath(path);
  return (
    <div class="stack">
      <p>
        <a href="/account">{text.account}</a>
        {" · "}
        <a href="/account/sharing">{sharingCard.link}</a>
      </p>
      {ref === null ? <Campaigns api={api} /> : <Rules api={api} campaignRef={ref} />}
    </div>
  );
}

function Campaigns({ api }: { api: AccountApi }) {
  const load = useRead(api.houseRuleCampaigns ? () => api.houseRuleCampaigns!() : null, [api]);
  return (
    <Shell load={load}>
      {(campaigns: HouseRuleCampaign[]) =>
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
                  <span class="small">{text.campaignLine(c.serverName, c.rules, c.sets)}</span>
                  <a
                    href={`/account/house-rules/${encodeURIComponent(c.ref)}`}
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

function Rules({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const load = useRead(api.houseRules ? () => api.houseRules!(campaignRef) : null, [
    api,
    campaignRef,
  ]);
  return (
    <Shell load={load}>
      {(found: HouseRules) => (
        <>
          <p>
            <a href="/account/house-rules">{text.allCampaigns}</a>
          </p>
          <CampaignNav api={api} campaignRef={campaignRef} page="houseRules" name={found.name} />
          <h2>{found.name}</h2>
          <p class="small">{text.leadOneCampaign}</p>
          {found.rules.length === 0 ? (
            <p class="panel" role="status">
              {found.sets.length > 0 ? text.emptyButSet : text.empty}
            </p>
          ) : (
            <>
              <p class="small muted">{text.numbersStay}</p>
              <ul class="house-rules" aria-label={found.name}>
                {found.rules.map((r) => (
                  <li key={r.number}>
                    <span>{text.ruleLine(r.number, r.rule)}</span>
                    {r.supersedes && <span class="small muted">{text.instead(r.supersedes)}</span>}
                    {r.fromSet && <span class="small muted">{text.fromSet(r.fromSet)}</span>}
                  </li>
                ))}
              </ul>
              <Download api={api} campaignRef={campaignRef} fileName={found.fileName} />
            </>
          )}
          {HOUSE_FILE_LIVE && api.houseFile && (
            <LazyPanel
              load={() => import("./HouseFilePanel")}
              what="the house-rules file"
              props={{ api, campaignRef }}
            />
          )}
          {RULE_SHARING_LIVE && api.ruleSharing && (
            <LazyPanel
              load={() => import("./RuleSharing")}
              what="sharing"
              props={{ api, campaignRef }}
            />
          )}
          {found.sets.length > 0 && (
            <section aria-labelledby="following-heading">
              <h3 id="following-heading">{text.setsHeading}</h3>
              <p class="small">{text.setsNote}</p>
              {found.sets.map((s) => (
                <div key={`${s.name}-${s.version}`}>
                  <p>
                    <strong>{text.setLine(s.cite)}</strong>
                  </p>
                  <ul class="house-rules">
                    {s.rules.map((r) => (
                      <li key={r.number}>
                        <span>{text.setRuleLine(r.number, r.rule)}</span>
                        {r.supersedes && (
                          <span class="small muted">{text.instead(r.supersedes)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}
        </>
      )}
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
  if (!api.houseRulesFile) return null;
  async function save() {
    if (busy.current) return;
    busy.current = true;
    setState("busy");
    try {
      const blob = await api.houseRulesFile!(campaignRef);
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
      <button type="button" class="button secondary" onClick={() => void save()}>
        {state === "busy" ? text.downloading : text.download}
      </button>{" "}
      <span class="small muted">{text.downloadNote}</span>{" "}
      <span role="status" class="warn">
        {state === "failed" ? text.downloadFailed : ""}
      </span>
    </p>
  );
}

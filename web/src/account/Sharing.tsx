/** @jsxImportSource preact */
// The Shared rules page (#2460 part 2): for each campaign you run, the house-rules sets and
// homebrew packs it shares and the ones it uses from other tables. Read-only; every line says where
// to change it in Discord. The API only answers for the campaigns you run.
import { useEffect, useState } from "preact/hooks";

import { problem, text } from "../content/sharing";
import { RULE_UPDATES_LIVE } from "../content/rule-updates-switch";
import CampaignNav from "./CampaignNav";
import LazyPanel from "./LazyPanel";
import {
  ApiError,
  type AccountApi,
  type FollowedRuleSet,
  type SharedRuleSet,
  type SharedRulesCampaign,
} from "./api";

// The packs a campaign follows from other tables (#4872): loaded only while RULE_UPDATES_LIVE.
const loadPackUpdates = () => import("./PackUpdates");

type Load =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; value: SharedRulesCampaign[] };

const change = (kind: string): string => (kind === "homebrew" ? text.changePacks : text.changeRules);

/** The campaign in the address: `/account/sharing/<server>.<campaign>`, or null for the overview (#5039). */
export function refFromSharingPath(path: string): string | null {
  const match = /^\/account\/sharing\/([^/]+?)\/?$/.exec(path);
  if (!match) return null;
  // A malformed address names no campaign the user runs: the page then says so, rather than unmounting.
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return "";
  }
}

function Shares({ items }: { items: SharedRuleSet[] }) {
  return (
    <>
      <h3>{text.sharesHeading}</h3>
      <ul class="shared-rules">
        {items.map((s, i) => (
          // Two sets can share a name, so the place in the list is part of the key.
          <li key={`${i}:${s.kind}:${s.name}`}>
            <strong>{s.name}</strong>
            <ul class="facts small muted">
              <li>{text.kinds[s.kind] ?? text.kinds.homebrew}</li>
              <li>{text.version(s.version)}</li>
              <li>{s.shared ? text.sharing : text.stopped}</li>
              {s.shared && <li>{s.hasCode ? text.codeOn : text.codeOff}</li>}
            </ul>
            <p class="small muted">{change(s.kind)}</p>
          </li>
        ))}
      </ul>
    </>
  );
}

function Uses({ items }: { items: FollowedRuleSet[] }) {
  return (
    <>
      <h3>{text.followsHeading}</h3>
      <ul class="shared-rules">
        {items.map((f, i) => (
          <li key={`${i}:${f.kind}:${f.name}`}>
            <strong>{f.name}</strong>
            <ul class="facts small muted">
              <li>{text.kinds[f.kind] ?? text.kinds.homebrew}</li>
              <li>{text.from(f.publisher)}</li>
              <li>{text.version(f.version)}</li>
              <li>{f.updating ? text.updating : text.noLongerUpdated}</li>
            </ul>
            <p class="small muted">{change(f.kind)}</p>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function SharingPage({ api, campaignRef = null }: { api: AccountApi; campaignRef?: string | null }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    if (!api.sharedRules) {
      setLoad({ kind: "error", message: text.failed });
      return;
    }
    api.sharedRules().then(
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
  }, [api]);

  // With a campaign in the address, the page is that campaign's (#5039): its navigation, and only its sets and packs.
  const shown = load.kind === "ready" && campaignRef !== null ? load.value.filter((c) => c.ref === campaignRef) : null;
  const campaignName = shown?.[0]?.name ?? null;
  return (
    <div class="stack">
      {campaignRef !== null ? (
        <CampaignNav api={api} campaignRef={campaignRef} page="sharedRules" name={campaignName} />
      ) : (
        <nav aria-label={text.crumbsLabel}>
          <ol class="crumbs">
            <li>
              <a href="/account">{text.account}</a>
            </li>
            <li>
              <span aria-current="page">{text.heading}</span>
            </li>
          </ol>
        </nav>
      )}
      {load.kind === "loading" && (
        <p class="muted" role="status">
          {text.loading}
        </p>
      )}
      {load.kind === "signed-out" && (
        <div class="panel" role="status">
          <p>{text.signedOut}</p>
        </div>
      )}
      {load.kind === "error" && (
        <p class="warn" role="alert">
          {load.message}
        </p>
      )}
      {load.kind === "ready" &&
        ((shown ?? load.value).length === 0 ? (
          <p class="panel" role="status">
            {text.empty}
          </p>
        ) : (
          <>
            <p class="lead">{text.lead}</p>
            {(shown ?? load.value).map((c) => (
              <section key={c.ref} class="panel" aria-labelledby={`shared-${c.ref}`}>
                <h2 id={`shared-${c.ref}`}>
                  {campaignRef === null ? <a href={`/account/sharing/${encodeURIComponent(c.ref)}`}>{c.name}</a> : c.name}
                </h2>
                <p class="small muted">{text.server(c.serverName)}</p>
                <p class="small">
                  <a href={`/account/house-rules/${encodeURIComponent(c.ref)}`}>{text.openHouseRules}</a>{" · "}
                  <a href={`/account/homebrew/${encodeURIComponent(c.ref)}`}>{text.openHomebrew}</a>
                </p>
                {/* The API lists a campaign only when it shares or follows something, so there is
                    no empty case to word here. */}
                {c.publishes.length > 0 && <Shares items={c.publishes} />}
                {c.follows.length > 0 && <Uses items={c.follows} />}
                {RULE_UPDATES_LIVE && <LazyPanel load={loadPackUpdates} what="the packs DMbot follows" props={{ api, campaignRef: c.ref }} />}
              </section>
            ))}
          </>
        ))}
    </div>
  );
}

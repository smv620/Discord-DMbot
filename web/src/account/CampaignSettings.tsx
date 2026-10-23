/** @jsxImportSource preact */
// The Settings group of a campaign's page (#2461, #2484): the game masters' current choices, and the
// three the site can change (who can see the DM screen, how much DMbot says, how long transcripts are
// kept). A choice is sent to the bot through the website's queue; the bot applies it exactly as
// Discord's button does and the page waits for its answer. Hand over, pause and delete stay in
// Discord, and their steps are shown only to the campaign's owner.
import { useEffect, useRef, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { ApiError, type AccountApi, type CampaignPage, type CampaignSettingName } from "./api";

/** How long between looks at the bot's answer, then the last repeats. */
export const POLL_MS = [400, 800, 1500, 2500];
/** After this long waiting the page says it is slow; after the next, that the bot is away. */
export const SLOW_AFTER_MS = 8000;
export const OFFLINE_AFTER_MS = 30000;

type Phase =
  | { kind: "idle" }
  | { kind: "saving"; slow: boolean }
  | { kind: "offline" }
  | { kind: "saved"; note: string }
  | { kind: "error"; message: string }
  | { kind: "confirm"; value: string };

const current = (s: NonNullable<CampaignPage["settings"]>): Record<CampaignSettingName, string> => ({
  visibility: s.visibility,
  level: s.level === "quiet" ? "quiet" : "normal", // "chatty" is an old choice that Discord no longer offers
  retention: s.keepDays === null ? "forever" : String(s.keepDays),
});

const label = (setting: CampaignSettingName, value: string): string =>
  words.options[setting]?.find(([v]) => v === value)?.[1] ?? value;

/** A shorter limit than the one kept now: it deletes old sessions, so it is asked about first. */
function shorter(now: string, next: string): boolean {
  if (next === "forever") return false;
  if (now === "forever") return true;
  return Number(next) < Number(now);
}

/** Discord's words with its `**bold**` marks taken out: the page shows plain text. */
const plain = (note: string): string => note.replaceAll("**", "");

function refusal(kind: string): string {
  if (kind === "not-allowed") return words.notAllowed;
  if (kind === "not-found") return words.campaignGone;
  if (kind === "bad-value" || kind === "bad-setting") return words.badChoice;
  if (kind === "too-soon") return words.tooSoon;
  return words.failed;
}

function answerWords(error: string, note: string): string {
  if (error === "refused") return `${words.refused} ${plain(note)}`;
  if (error === "not_allowed") return words.notAllowed;
  if (error === "not_found") return words.campaignGone;
  if (error === "bad_value" || error === "bad_setting") return words.badChoice;
  return words.failed;
}

function Control({
  id,
  setting,
  value,
  phase,
  onChoose,
  onConfirm,
  onKeep,
}: {
  id: string;
  setting: CampaignSettingName;
  value: string;
  phase: Phase;
  onChoose: (v: string) => void;
  onConfirm: (v: string) => void;
  onKeep: () => void;
}) {
  const busy = phase.kind === "saving";
  const options = words.options[setting] ?? [];
  const line =
    phase.kind === "saving"
      ? phase.slow
        ? `${words.saving} ${words.slow}`
        : words.saving
      : phase.kind === "offline"
        ? words.offline
        : phase.kind === "saved"
          ? `${words.saved} ${phase.note}`.trim()
          : phase.kind === "error"
            ? phase.message
            : "";
  return (
    <li>
      <div role="group" aria-labelledby={id} class="stack">
        <span class="campaign-name" id={id}>
          {words.controls[setting]}
        </span>
        <span class="row">
          {options.map(([v, text]) => (
            <button
              key={v}
              type="button"
              class={v === value ? "button" : "button secondary"}
              aria-pressed={v === value}
              disabled={busy}
              onClick={() => v !== value && onChoose(v)}
            >
              {text}
            </button>
          ))}
        </span>
      </div>
      {phase.kind === "confirm" && (
        <div class="panel" role="group" aria-label={words.controls[setting]}>
          <p>{words.retentionWarning(label(setting, phase.value))}</p>
          <p class="row">
            <button type="button" class="button" onClick={() => onConfirm(phase.value)}>
              {words.deleteAfter(label(setting, phase.value))}
            </button>
            <button type="button" class="button secondary" onClick={onKeep}>
              {words.keepAsIs}
            </button>
          </p>
        </div>
      )}
      <p class={phase.kind === "error" ? "warn small" : "small muted"} role={phase.kind === "error" ? "alert" : "status"}>
        {line}
      </p>
    </li>
  );
}

export default function CampaignSettings({
  api,
  campaignRef,
  page,
}: {
  api: AccountApi;
  campaignRef: string;
  page: CampaignPage;
}) {
  const s = page.settings;
  const owner = page.role === "owner";
  const [values, setValues] = useState<Record<CampaignSettingName, string> | null>(s ? current(s) : null);
  const [phases, setPhases] = useState<Record<CampaignSettingName, Phase>>({
    visibility: { kind: "idle" },
    level: { kind: "idle" },
    retention: { kind: "idle" },
  });
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const set = (setting: CampaignSettingName, phase: Phase) =>
    live.current && setPhases((before) => ({ ...before, [setting]: phase }));

  async function send(setting: CampaignSettingName, value: string, confirm: boolean) {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return set(setting, { kind: "error", message: words.failed });
    set(setting, { kind: "saving", slow: false });
    let answer: string;
    try {
      answer = await api.setCampaignSetting(campaignRef, { setting, value, ...(confirm ? { confirm: true } : {}) });
    } catch (error) {
      return set(setting, { kind: "error", message: refusal(error instanceof ApiError ? error.kind : "server") });
    }
    const started = Date.now();
    for (let i = 0; live.current; i += 1) {
      await new Promise((r) => setTimeout(r, POLL_MS[Math.min(i, POLL_MS.length - 1)]));
      if (!live.current) return;
      const waited = Date.now() - started;
      let got;
      try {
        got = await api.campaignSettingAnswer(campaignRef, answer);
      } catch (error) {
        // The queue forgets a choice after an hour; before that a look that fails is tried again.
        if (error instanceof ApiError && error.kind === "not-found") return set(setting, { kind: "error", message: words.timedOut });
        if (waited > OFFLINE_AFTER_MS) return set(setting, { kind: "error", message: words.failed });
        continue;
      }
      if (got.state === "done") {
        setValues((before) => (before ? { ...before, [setting]: value } : before));
        return set(setting, { kind: "saved", note: plain(got.note) });
      }
      if (got.state === "failed") {
        if (got.error === "needs_confirm") return set(setting, { kind: "confirm", value });
        return set(setting, { kind: "error", message: answerWords(got.error, got.note) });
      }
      if (waited > OFFLINE_AFTER_MS) return set(setting, { kind: "offline" });
      if (waited > SLOW_AFTER_MS) set(setting, { kind: "saving", slow: true });
    }
  }

  function choose(setting: CampaignSettingName, value: string) {
    if (setting === "retention" && values && shorter(values.retention, value)) return set(setting, { kind: "confirm", value });
    void send(setting, value, false);
  }

  const settingRows: CampaignSettingName[] = ["visibility", "level", "retention"];
  const discordOnly: (keyof typeof words.paths)[] = owner ? ["handOver", "pause", "delete"] : [];
  return (
    <section class="stack" aria-labelledby="settings-heading" id="settings">
      <h2 id="settings-heading">{words.groups.settings}</h2>
      <p class="muted small">{words.settingsLead}</p>
      <ul class="campaigns">
        {values &&
          settingRows.map((setting) => (
            <Control
              key={setting}
              id={`setting-${setting}`}
              setting={setting}
              value={values[setting]}
              phase={phases[setting]}
              onChoose={(v) => choose(setting, v)}
              onConfirm={(v) => void send(setting, v, true)}
              onKeep={() => set(setting, { kind: "idle" })}
            />
          ))}
        <li>
          <span class="campaign-name">{words.settingsLabels.gameMasters}</span>
          {s && <span class="block">{words.gameMastersCount(s.gameMasters)}</span>}
          <span class="small muted block">{words.doThisInDiscord(words.paths.gameMasters)}</span>
        </li>
        {discordOnly.map((key) => (
          <li key={key}>
            <span class="campaign-name">{words.settingsLabels[key]}</span>
            {key === "pause" && s && <span class="block">{s.paused ? words.pausedYes : words.pausedNo}</span>}
            <span class="small muted block">{words.doThisInDiscord(words.paths[key])}</span>
            {key === "delete" && <span class="small muted block">{words.deleteWarning}</span>}
          </li>
        ))}
        {!owner && <li class="small muted">{words.ownerOnlyNote}</li>}
      </ul>
    </section>
  );
}

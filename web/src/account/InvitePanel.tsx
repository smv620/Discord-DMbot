/** @jsxImportSource preact */
// The D&D Beyond invite link on a campaign's page, for its game masters (#2985): paste, change or remove the campaign's
// invite link. The choice is queued for the bot like the other settings and the page follows the answer with the shared
// wait panel. The shape is checked here with Discord's own rule so a wrong paste is caught at once; the server and the
// bot check again. Only a DM ever gets this page's data, and the link is shown to them as Discord's own form does.
import { useEffect, useRef, useState } from "preact/hooks";

import { INVITE_PATTERN, invitePanel as words } from "../content/invite-words";
import { type AccountApi, ApiError, type CampaignSettingAnswer } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLLS = 40;

/** The bot's words with Discord's `**bold**` and `code` marks taken out: the page shows plain text. */
const plain = (note: string): string => note.replaceAll("**", "").replaceAll("`", "").replace(/\s*\n+\s*/g, " ").trim();

/** The standard form Discord keeps: `www`, no trailing slash. */
const standard = (link: string): string => {
  const code = /\/campaigns\/join\/([A-Za-z0-9]+)/.exec(link)?.[1] ?? "";
  return code ? `https://www.dndbeyond.com/campaigns/join/${code}` : link;
};

type Wait = { remove: boolean; state: CampaignSettingAnswer["state"] | "sending" | "timed_out"; note: string; since: number };

export default function InvitePanel({ api, campaignRef, invite, onChanged }: { api: AccountApi; campaignRef: string; invite: string; onChanged?: (invite: string) => void }) {
  const [current, setCurrent] = useState(invite);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const [wait, setWait] = useState<Wait | null>(null);
  const live = useRef(true);
  const field = useRef<HTMLInputElement>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => () => void (live.current = false), []);
  useEffect(() => {
    if (editing) field.current?.focus();
  }, [editing]);

  const busy = wait !== null && wait.state !== "done" && wait.state !== "failed" && wait.state !== "timed_out";

  const go = async (value: string): Promise<void> => {
    if (!api.setCampaignSetting || !api.campaignSettingAnswer) return;
    const remove = value === "";
    setEditing(false);
    setProblem("");
    const since = Math.floor(Date.now() / 1000);
    setWait({ remove, state: "sending", note: "", since });
    let queued = false;
    try {
      const id = await api.setCampaignSetting(campaignRef, { setting: "dndbeyond_invite", value });
      queued = true;
      for (let i = 0; i < POLLS && live.current; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const got = await api.campaignSettingAnswer(campaignRef, id);
        if (!live.current) return;
        setWait({ remove, state: got.state, note: got.note, since });
        if (got.state === "done") {
          setCurrent(value);
          onChanged?.(value);
          return;
        }
        if (got.state === "failed") return;
      }
      if (live.current) setWait({ remove, state: "timed_out", note: "", since });
    } catch (err) {
      if (queued) {
        // It was sent; only the look at the answer failed. Do not say it failed: it may well have worked.
        setWait({ remove, state: "timed_out", note: "", since });
        return;
      }
      setWait(null);
      setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    }
  };

  function save(e: Event) {
    e.preventDefault();
    const link = text.trim();
    if (!link) return setProblem(words.nothingYet);
    if (!INVITE_PATTERN.test(link)) return setProblem(words.notAnInvite);
    void go(standard(link));
  }

  function close() {
    setEditing(false);
    setProblem("");
    setText("");
    setTimeout(() => first.current?.focus(), 0);
  }

  const kind = wait?.remove ? "remove" : "set";
  return (
    <section class="stack" aria-labelledby="invite-heading">
      <h3 id="invite-heading">{words.heading}</h3>
      <p class="small">{words.intro}</p>
      <p>{current ? words.now(current) : words.none}</p>
      {problem && <p role="alert">{problem}</p>}
      {wait && wait.state === "timed_out" && <p role="status">{words.timedOut}</p>}
      {wait && wait.state === "done" && <p role="status">{words.done[kind]}</p>}
      {wait && wait.state !== "timed_out" && (
        <WorkProgress
          stage={wait.state === "done" ? words.done[kind] : wait.state === "working" ? words.working[kind] : words.waiting[kind]}
          startedAt={wait.since}
          state={wait.state === "failed" ? "failed" : wait.state === "done" ? "done" : "running"}
          reason={wait.state === "failed" ? `${words.failed} ${plain(wait.note) || words.failedNext} ${words.stillNow(current)}`.replace(/\s+/g, " ").trim() : null}
        />
      )}
      {!editing && (
        <p>
          <button type="button" class="button secondary" ref={first} disabled={busy} onClick={() => { setText(""); setProblem(""); setEditing(true); }}>
            {current ? words.change : words.add}
          </button>{" "}
          {current && (
            <button type="button" class="button secondary" disabled={busy} onClick={() => void go("")}>
              {words.remove}
            </button>
          )}
        </p>
      )}
      {editing && (
        <form class="stack" onSubmit={save} noValidate>
          <label>
            {words.label}
            <input type="url" ref={field} value={text} maxLength={200} autocomplete="off" aria-describedby="invite-help" onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)} />
          </label>
          <p class="small muted" id="invite-help">{words.help}</p>
          <p>
            <button type="submit" class="button" disabled={busy}>{words.save}</button>{" "}
            <button type="button" class="button secondary" onClick={close}>{words.cancel}</button>
          </p>
        </form>
      )}
    </section>
  );
}

/** @jsxImportSource preact */
// "Remove this speaker's lines" on the Transcripts page (#3305 PR 2, #3306): for a game master, the people without Discord
// that imported sessions kept by name, each with a button that removes everything that person said in this campaign. It asks
// once, queues the removal for the bot, and follows it with the shared wait (the stage, how long it has run, when it last
// moved). A player, an older API or a failed list shows nothing at all, and the panel never shows a line of a transcript.
import { useEffect, useRef, useState } from "preact/hooks";

import { SPEAKER_REMOVAL_LIVE } from "../content/speaker-removal-switch";
import { speakerRemovalWords as words } from "../content/speaker-removal-words";
import { type AccountApi, ApiError, type CampaignSpeaker, type SpeakerRemoval } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 2000;
const POLL_MAX_MS = 20_000;

export default function SpeakerRemovalPanel({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<CampaignSpeaker[] | "loading" | "hidden">("loading");
  const [asking, setAsking] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  // The one removal being followed: whose it is, its id and the last answer.
  const [open, setOpen] = useState<{ speaker: CampaignSpeaker; id: string; at: SpeakerRemoval | null } | null>(null);
  const [stopped, setStopped] = useState(false);
  const errors = useRef(0);
  const sending = useRef(false); // (a ref, so two quick presses before the next render cannot both send)

  /** Reads the list. Only the first read can hide the panel (a player, an older API or a failed list: nothing to show);
   * a later one that fails keeps what is shown, so a removal's result and a "nothing left" note never vanish with it. */
  const load = async (first = false): Promise<void> => {
    try {
      setList(await api.campaignSpeakers!(campaignRef));
    } catch {
      if (first) setList("hidden");
    }
  };
  useEffect(() => {
    setList("loading");
    setOpen(null);
    setAsking(null);
    if (!SPEAKER_REMOVAL_LIVE || !api.campaignSpeakers || !api.removeSpeaker || !api.speakerRemoval) {
      setList("hidden");
      return;
    }
    void load(true);
  }, [api, campaignRef]);

  // Follow the open removal until it is done or failed, asking less often while nothing moves.
  useEffect(() => {
    if (!open || !api.speakerRemoval) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let idle = 0;
    let seen = -1;
    const look = async (): Promise<void> => {
      try {
        const got = await api.speakerRemoval!(campaignRef, open.id);
        if (!live) return;
        errors.current = 0;
        idle = got.updatedAt === seen ? idle + 1 : 0;
        seen = got.updatedAt ?? -1;
        setOpen((o) => (o && o.id === open.id ? { ...o, at: got } : o));
        if (got.state === "done") return void load();
        if (got.state === "failed") return;
        timer = setTimeout(look, Math.min(POLL_MS * 2 ** Math.min(idle, 3), POLL_MAX_MS));
      } catch {
        if (!live) return;
        errors.current += 1; // one lost answer is not the end: a few more tries before the page says it stopped looking
        if (errors.current >= 3) setStopped(true);
        else timer = setTimeout(look, POLL_MS * 2 ** errors.current);
      }
    };
    timer = setTimeout(look, POLL_MS);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, campaignRef, open?.id]);

  if (list === "hidden" || list === "loading") return null;
  // Nobody to remove, and nothing being followed: nothing to show.
  if (list.length === 0 && !open) return null;

  const failed = open?.at?.state === "failed";
  const done = open?.at?.state === "done";
  const pending = open !== null && !failed && !done && !stopped;

  const remove = async (speaker: CampaignSpeaker): Promise<void> => {
    if (sending.current) return;
    sending.current = true;
    setAsking(null);
    setProblem("");
    setBusy(true);
    try {
      const got = await api.removeSpeaker!(campaignRef, speaker.id);
      errors.current = 0;
      setStopped(false);
      setOpen({ speaker, id: got.id, at: null });
    } catch (err) {
      // "Nothing left" is information, not a failure: the list is read again so the person goes.
      if (err instanceof ApiError && err.kind === "nothing") {
        setProblem(words.nothing);
        void load();
      } else {
        setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
        if (err instanceof ApiError && err.kind === "not-found") void load(); // (they are not in this campaign any more: the list says so)
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  const outside = list.filter((s) => s.member !== true);
  const ticked = list.filter((s) => s.member === true);

  /** One group of people with their buttons, under its own heading. */
  const group = (id: string, heading: string, intro: string, people: CampaignSpeaker[]) =>
    people.length === 0 ? null : (
      <div class="stack" role="group" aria-labelledby={id}>
        <h3 id={id}>{heading}</h3>
        <p class="small">{intro}</p>
        <ul class="campaigns">
          {people.map((s) => (
            <li key={s.id}>
              <span class="campaign-name">{s.name}</span>
              <span class="small">{words.linesLabel(s.lines)}</span>
              {asking === s.id ? (
                <div class="stack" role="group" aria-label={words.confirm(s.name, s.member === true)}>
                  <p>{words.confirm(s.name, s.member === true)}</p>
                  <p>
                    <button type="button" class="button" disabled={busy || pending} onClick={() => void remove(s)}>
                      {words.confirmYes}
                    </button>{" "}
                    <button type="button" class="button secondary" onClick={() => setAsking(null)}>
                      {words.keep}
                    </button>
                  </p>
                </div>
              ) : (
                <button type="button" class="button secondary" disabled={busy || pending} aria-label={words.buttonFor(s.name)} onClick={() => setAsking(s.id)}>
                  {words.button}
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    );

  return (
    <section class="stack" aria-labelledby="sr-heading">
      <h2 id="sr-heading">{words.heading}</h2>
      {problem && <p role="alert">{problem}</p>}
      {open && (
        <div class="stack">
          {done ? (
            <p role="status">{words.done(open.at?.lines ?? open.speaker.lines, open.at?.sessions ?? 0)}</p>
          ) : (
            <WorkProgress
              stage={words.stage[open.at?.stage ?? "removing"] ?? words.stage.removing ?? ""}
              startedAt={open.at?.startedAt ?? null}
              updatedAt={open.at?.updatedAt ?? null}
              serverNow={open.at?.serverNow ?? null}
              state={failed ? "failed" : open.at?.state === "stalled" ? "stalled" : "running"}
              frozen={stopped}
              reason={failed ? words.failedDo : null}
            />
          )}
        </div>
      )}
      {group("sr-outside", words.outsideHeading, words.intro, outside)}
      {group("sr-ticked", words.memberHeading, words.memberIntro, ticked)}
    </section>
  );
}

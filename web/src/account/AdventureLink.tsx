/** @jsxImportSource preact */
// "📖 In my adventure…" on a name's details on the Story Graph (#3694 slice G2, behind ADVENTURE_LIVE; core #5328): Discord's picker on a name's card. The
// DM says which entry of their adventure book a name is (or takes the link off): DMbot lists the book's entries, twenty-five at a time, and the DM
// picks; one Undo takes the last change back. Every ask is queued for the bot and polled (stage, how long, when it last moved). The book's entry
// names are shown on this panel only: never in an address, a log or storage. The calls are the adventure's own, loaded the first time it opens.
import { useEffect, useRef, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AccountApi, type AdventureApi, ApiError, type AdventureMatch, type AdventureOption } from "./api";
import WorkProgress from "./WorkProgress";

const FIRST_MS = 500;
const EVERY_MS = 1500;
const LOOKS = 60;

type Body = Parameters<AdventureApi["adventureMatch"]>[1];

export default function AdventureLink({ api: site, refId, entity }: { api: AccountApi; refId: string; entity: { id: string; name: string } }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<AdventureMatch | null>(null);
  const [linked, setLinked] = useState<AdventureOption[]>([]);
  const [options, setOptions] = useState<AdventureOption[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ offset: 0, size: 25 });
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState("");
  const [undoOf, setUndoOf] = useState<string | null>(null);
  const [looked, setLooked] = useState(false);
  const epoch = useRef(0);
  const sending = useRef(false);
  const runs = useRef(0); // which ask holds the lock: a follow-up ask started from `done` must not be released by the one that started it
  const calls = useRef<AdventureApi | null>(null);
  const waitBox = useRef<HTMLDivElement>(null);
  const headBox = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    return () => {
      epoch.current += 1; // leaving the page, or another name, ends the watching
      sending.current = false;
    };
  }, [site, refId, entity.id]);
  useEffect(() => {
    if (busy) waitBox.current?.focus();
  }, [busy]);

  /** Queue one ask and poll it to its end; `done` gets the finished job and its id. */
  async function run(body: Body, done: (got: AdventureMatch, id: string) => void, slow: string = w.matchSlow): Promise<void> {
    if (sending.current) return;
    sending.current = true;
    const ask = (runs.current += 1);
    const mine = epoch.current;
    const here = (): boolean => mine === epoch.current;
    setBusy(true);
    setProblem("");
    setSeen(null);
    const stop = (text: string): void => {
      if (!here()) return;
      setBusy(false);
      setProblem(text);
    };
    try {
      calls.current ??= (await site.adventureApi?.()) ?? null;
      const api = calls.current;
      if (!api || !here()) return stop(w.matchRefused.server ?? "");
      let id: string;
      try {
        id = (await api.adventureMatch(refId, body)).match;
      } catch (error) {
        return stop(w.matchRefused[error instanceof ApiError ? error.kind : "network"] ?? w.matchRefused.server ?? "");
      }
      let errors = 0;
      for (let i = 0; i < LOOKS; i += 1) {
        await new Promise((ok) => setTimeout(ok, i === 0 ? FIRST_MS : EVERY_MS));
        if (!here()) return;
        try {
          const got = await api.adventureMatching(refId, id);
          errors = 0;
          if (!here()) return;
          setSeen(got);
          if (got.state === "failed") return stop(got.error === "gone" ? w.linkGone : (w.matchFailed[got.error] ?? w.matchFailed.failed ?? ""));
          if (got.state === "done") {
            setBusy(false);
            sending.current = false; // (so that `done` can ask again)
            done(got, id);
            return;
          }
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "signed-out")) return stop(slow);
          errors += 1;
          if (errors >= 3) return stop(slow);
        }
      }
      stop(slow);
    } finally {
      if (here() && runs.current === ask) sending.current = false;
    }
  }

  /** Read the entry's links and the book's entries (the first page, or the page at `skip`, added to what is shown). */
  function look(skip: number, after?: () => void): void {
    setOpen(true);
    void run({ job: "options", entity: entity.id, skip }, (got) => {
      const a = got.answer;
      setLinked(a?.linked ?? []);
      setOptions((now) => (skip === 0 ? (a?.options ?? []) : [...now, ...(a?.options ?? [])]));
      setTotal(a?.total ?? 0);
      setPage({ offset: a?.offset ?? skip, size: a?.pageSize ?? 25 });
      setLooked(true);
      after?.();
      setTimeout(() => headBox.current?.focus(), 0);
    });
  }

  function change(job: "pick" | "unpick", plan: AdventureOption): void {
    setSaid("");
    void run({ job, entity: entity.id, plan: plan.planId }, (got, id) => {
      const written = got.answer?.written ?? 0;
      const refusal = got.answer?.refusal ?? "";
      setUndoOf(got.canUndo ? id : null);
      look(0, () => {
        if (refusal) setProblem(w.matchRefusals[refusal] ?? w.matchRefusals.failed ?? "");
        else setSaid(job === "pick" ? w.linkLinked(plan.name) : written > 0 ? w.linkTakenOff : w.linkAlreadyOff);
      });
    }, w.matchSlowSave);
  }

  function undo(): void {
    if (!undoOf) return;
    void run({ job: "undo", source: undoOf }, (got) => {
      const note = got.answer?.note ?? "";
      setUndoOf(null);
      look(0, () => setSaid(note ? (w.matchUndoNotes[note] ?? w.matchUndone) : w.matchUndone));
    });
  }

  if (!site.adventureApi) return null;
  const next = page.offset + page.size;
  return (
    <div class="stack">
      {!open && (
        <button type="button" class="button secondary" onClick={() => look(0)}>
          {w.linkOpen}
        </button>
      )}
      {open && (
        <section class="stack" aria-labelledby={`adv-link-${entity.id}`}>
          <h4 id={`adv-link-${entity.id}`} tabIndex={-1} ref={headBox}>
            {w.linkWhich(entity.name)}
          </h4>
          {busy && (
            <div ref={waitBox} tabIndex={-1}>
              <WorkProgress stage={w.matchStages[seen?.state ?? "waiting"] ?? w.linkLooking} startedAt={seen?.createdAt} updatedAt={seen?.updatedAt} serverNow={seen?.serverNow} state="running" />
            </div>
          )}
          {problem && (
            <p class="warn" role="alert">
              {problem}
            </p>
          )}
          <p role="status">{said}</p>
          {looked && !busy && (
            <div class="stack">
              {linked.length > 0 && (
                <ul class="stack">
                  {linked.map((l) => (
                    <li key={l.planId} style={{ overflowWrap: "anywhere" }}>
                      {w.linkNow(l.name, l.chapter)}{" "}
                      <button type="button" class="button secondary" aria-label={`${w.linkTakeOff} ${l.name}`} onClick={() => change("unpick", l)}>
                        {w.linkTakeOff}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {total === 0 && linked.length === 0 && <p>{w.linkNothing}</p>}
              {options.length > 0 && (
                <ul class="stack">
                  {options.map((o) => (
                    <li key={o.planId} style={{ overflowWrap: "anywhere" }}>
                      {w.linkNow(o.name, o.chapter)}{" "}
                      <button type="button" class="button secondary" aria-label={`${w.linkPick}: ${o.name}`} onClick={() => change("pick", o)}>
                        {w.linkPick}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {next < total && (
                <button type="button" class="button secondary" onClick={() => look(next)}>
                  {w.linkMore}
                </button>
              )}
            </div>
          )}
          <div class="row">
            {!busy && undoOf && (
              <button type="button" class="button secondary" onClick={undo}>
                {w.matchUndo}
              </button>
            )}
            <button
              type="button"
              class="button secondary"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setLooked(false);
                setSaid("");
                setProblem("");
                setUndoOf(null);
              }}
            >
              {w.linkClose}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

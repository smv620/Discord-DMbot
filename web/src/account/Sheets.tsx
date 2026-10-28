/** @jsxImportSource preact */
// "Character sheets" on a campaign's page (#2816; the character-sheets row of #2554). A player sees their own
// characters' sheets, links one, or forgets it; a DM sees every sheet, read only, and can ask for them to
// be read again. Each choice is queued for the bot and this waits for its answer, showing what DMbot is doing, how
// long it has run and when it last moved (#2564). The page never fetches a character's link: the bot does. Loaded
// lazily from the campaign page, and only while SHEETS_LIVE is on.
import { useEffect, useRef, useState } from "preact/hooks";

import { sheetWords as w } from "../content/sheets-words";
import { stageWords } from "../content/work-progress";
import {
  ApiError,
  type AccountApi,
  type CharacterSheets,
  type SheetCharacter,
  type SheetChoice,
} from "./api";
import {
  GIVE_UP_MS,
  LATE_POLL_MS,
  OFFLINE_AFTER_MS,
  POLL_MS,
} from "./settings-timing";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";

type Report = { ok: boolean; text: string } | null;

const problem = (kind: string): string =>
  Object.hasOwn(w.problems, kind)
    ? (w.problems[kind] as string)
    : (w.problems.server as string);
const ENDS = ["not-allowed", "not-found", "signed-out"]; // looking again will not mend these

export default function CharacterSheetsSection({
  api,
  campaignRef,
}: {
  api: AccountApi;
  campaignRef: string;
}) {
  const [data, setData] = useState<CharacterSheets | "loading" | "failed">(
    "loading",
  );
  const [report, setReport] = useState<Report>(null);
  const [link, setLink] = useState<Record<string, string>>({});
  // What a player has typed in per character, so a re-render does not clear a half-typed form.
  const [typed, setTyped] = useState<Record<string, TypedValues>>({});
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  // What the server last said moved: its stage and update time. The wait counts as quiet only when these stop
  // changing (not every poll), so a bot that has gone quiet shows as stalled (#2564).
  const [moved, setMoved] = useState("");
  const flight = useRef(false);
  const live = useRef(true);
  const seen = useObservedWait(busy, moved);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const read = async (): Promise<void> => {
    if (!api.campaignSheets) return setData("failed");
    try {
      const got = await api.campaignSheets(campaignRef);
      if (live.current) setData(got);
    } catch (error) {
      if (live.current) setData("failed");
      if (error instanceof ApiError && ENDS.includes(error.kind)) return;
    }
  };
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  // One choice at a time: a press while one is on its way is ignored. The answer is looked for until the bot has
  // answered or the minute is up; then the page says so and reads the list again, so nothing is tried twice.
  const send = async (
    entity: string,
    choice: SheetChoice,
    done: string,
  ): Promise<void> => {
    if (flight.current || !api.chooseSheet || !api.sheetAnswer) return;
    flight.current = true;
    setBusy(true);
    setReport(null);
    setMoved("");
    setStage(w.stage.waiting as string);
    try {
      const id = await api.chooseSheet(campaignRef, entity, choice);
      const started = Date.now();
      for (let i = 0; live.current; i += 1) {
        await new Promise((r) =>
          setTimeout(
            r,
            Date.now() - started > OFFLINE_AFTER_MS
              ? LATE_POLL_MS
              : POLL_MS[Math.min(i, POLL_MS.length - 1)],
          ),
        );
        if (!live.current) return;
        const final = Date.now() - started > GIVE_UP_MS;
        let got: Awaited<
          ReturnType<NonNullable<AccountApi["sheetAnswer"]>>
        > | null = null;
        try {
          got = await api.sheetAnswer(campaignRef, id);
          if (got) setMoved(`${got.stage}|${got.updatedAt ?? ""}`);
        } catch (error) {
          if (error instanceof ApiError && ENDS.includes(error.kind))
            throw error;
        }
        if (got?.state === "done") {
          setStage(null);
          setReport({ ok: true, text: done });
          await read();
          return;
        }
        if (got?.state === "failed") throw new ApiError("server");
        if (got) setStage(stageWords(w.stage, got.stage));
        if (final) {
          setReport({ ok: false, text: w.quiet });
          await read();
          return;
        }
      }
    } catch (error) {
      if (live.current)
        setReport({
          ok: false,
          text: problem(error instanceof ApiError ? error.kind : "server"),
        });
    } finally {
      flight.current = false;
      if (live.current) {
        setBusy(false);
        setStage(null);
      }
    }
  };

  if (data === "loading")
    return (
      <p class="muted" role="status">
        {w.loading}
      </p>
    );
  if (data === "failed")
    return (
      <p class="warn" role="alert">
        {w.failedToLoad}
      </p>
    );

  const mine = data.sheets.filter((s) => s.mine);
  const others = data.sheets.filter((s) => !s.mine);
  return (
    <section class="stack" aria-labelledby="sheets-heading" id="sheets">
      <h3 id="sheets-heading">{w.heading}</h3>
      <p class="small">{w.lead}</p>
      <p role="status">{report?.ok ? report.text : ""}</p>
      <p class="warn small" role="alert">
        {report && !report.ok ? report.text : ""}
      </p>
      {busy && (
        <WorkProgress
          stage={stage ?? (w.stage.waiting as string)}
          startedAt={seen.startedAt}
          updatedAt={seen.movedAt}
          state={seen.quiet ? "stalled" : "running"}
          progressUnknown
          whatToDo={w.quiet}
        />
      )}
      {data.sheets.length === 0 && <p class="muted">{w.none}</p>}
      {mine.length > 0 && (
        <ul class="stack" aria-label={w.heading}>
          {mine.map((s) => (
            <li key={s.entityId}>
              <Character
                sheet={s}
                shown={true}
                busy={busy}
                linkValue={link[s.entityId] ?? ""}
                onLinkValue={(v) =>
                  setLink((now) => ({ ...now, [s.entityId]: v }))
                }
                onLink={() =>
                  void send(
                    s.entityId,
                    { action: "link", url: (link[s.entityId] ?? "").trim() },
                    w.linkSaved,
                  )
                }
                onForget={() =>
                  void send(s.entityId, { action: "forget" }, w.forgetDone)
                }
                typedValue={typed[s.entityId] ?? EMPTY_TYPED}
                onTypedValue={(patch) =>
                  setTyped((now) => ({
                    ...now,
                    [s.entityId]: { ...(now[s.entityId] ?? EMPTY_TYPED), ...patch },
                  }))
                }
                onTyped={(choice, done) => void send(s.entityId, choice, done)}
                onProblem={(text) => setReport({ ok: false, text })}
              />
            </li>
          ))}
        </ul>
      )}
      {data.isDm && (
        <>
          <p class="small muted">{w.dmOnly}</p>
          {others.length > 0 && (
            <ul class="stack" aria-label={w.heading}>
              {others.map((s) => (
                <li key={s.entityId}>
                  <Character
                    sheet={s}
                    shown={false}
                    busy={busy}
                    linkValue=""
                    onLinkValue={() => {}}
                    onLink={() => {}}
                    onForget={() => {}}
                    typedValue={EMPTY_TYPED}
                    onTypedValue={() => {}}
                    onTyped={() => {}}
                    onProblem={() => {}}
                  />
                </li>
              ))}
            </ul>
          )}
          <p>
            <button
              type="button"
              class="button secondary"
              aria-disabled={busy}
              onClick={() =>
                !busy &&
                void send("-", { action: "read_again" }, w.readAgainDone)
              }
            >
              {w.readAgain}
            </button>
          </p>
          <p class="small muted">{w.readAgainNote}</p>
        </>
      )}
    </section>
  );
}

/** What a player types in for a character with no sheet linked (#2832). Level is text until it is checked. */
type TypedValues = { className: string; level: string; species: string; names: string };
const EMPTY_TYPED: TypedValues = { className: "", level: "", species: "", names: "" };

function Character({
  sheet,
  shown,
  busy,
  linkValue,
  onLinkValue,
  onLink,
  onForget,
  typedValue,
  onTypedValue,
  onTyped,
  onProblem,
}: {
  sheet: SheetCharacter;
  shown: boolean;
  busy: boolean;
  linkValue: string;
  onLinkValue: (value: string) => void;
  onLink: () => void;
  onForget: () => void;
  typedValue: TypedValues;
  onTypedValue: (patch: Partial<TypedValues>) => void;
  onTyped: (choice: SheetChoice, done: string) => void;
  onProblem: (text: string) => void;
}) {
  const summary = sheet.summary;
  const classes =
    summary?.classes.map((c) => `${c.name} ${c.level}`).join(", ") ?? "";
  return (
    <div class="stack">
      <p>
        <strong>{shown ? w.mine(sheet.name) : sheet.name}</strong>{" "}
        <span class="small">
          {summary ? w.summary(summary.level, classes) : w.notYet}
        </span>
      </p>
      {sheet.url && (
        <p class="small">
          {w.linkLabel(sheet.name)}: <a href={sheet.url}>{sheet.url}</a>{" "}
          <span class="muted">{w.onlyYou}</span>
        </p>
      )}
      {shown && (
        <form
          class="row"
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy && linkValue.trim() !== "") onLink();
          }}
        >
          <label>
            {w.linkLabel(sheet.name)}
            <input
              type="url"
              value={linkValue}
              placeholder={w.linkPlaceholder}
              onInput={(e) => onLinkValue((e.target as HTMLInputElement).value)}
            />
          </label>
          <p class="small muted">{w.linkHelp}</p>
          <button type="submit" class="button" aria-disabled={busy}>
            {w.link}
          </button>
          {sheet.source && (
            <button
              type="button"
              class="button secondary"
              aria-disabled={busy}
              onClick={() => !busy && onForget()}
            >
              {w.forget}
            </button>
          )}
        </form>
      )}
      {shown && (
        <details>
          <summary>{w.typedSummary}</summary>
          <form
            class="stack"
            onSubmit={(e) => {
              e.preventDefault();
              if (busy) return;
              const className = typedValue.className.trim();
              if (className === "") return onProblem(w.typedNeedsClass);
              const level = typedValue.level.trim();
              const n = Number(level);
              if (!/^\d{1,2}$/.test(level) || n < 1 || n > 20)
                return onProblem(w.typedBadLevel);
              onTyped(
                {
                  action: "typed",
                  className,
                  level: n,
                  species: typedValue.species.trim(),
                  names: typedValue.names.trim(),
                },
                w.typedSaved +
                  (sheet.source === "dndbeyond" ? w.typedReplacedLink : ""),
              );
            }}
          >
            <label>
              {w.typedClass}
              <input
                type="text"
                required
                maxLength={60}
                value={typedValue.className}
                placeholder={w.typedClassHint}
                onInput={(e) =>
                  onTypedValue({ className: (e.target as HTMLInputElement).value })
                }
              />
            </label>
            <label>
              {w.typedLevel}
              <input
                type="text"
                inputMode="numeric"
                maxLength={2}
                value={typedValue.level}
                placeholder={w.typedLevelHint}
                onInput={(e) =>
                  onTypedValue({ level: (e.target as HTMLInputElement).value })
                }
              />
            </label>
            <label>
              {w.typedSpecies}
              <input
                type="text"
                maxLength={60}
                value={typedValue.species}
                placeholder={w.typedSpeciesHint}
                onInput={(e) =>
                  onTypedValue({ species: (e.target as HTMLInputElement).value })
                }
              />
            </label>
            <label>
              {w.typedNames}
              <textarea
                maxLength={1500}
                value={typedValue.names}
                placeholder={w.typedNamesHint}
                onInput={(e) =>
                  onTypedValue({ names: (e.target as HTMLTextAreaElement).value })
                }
              />
            </label>
            {sheet.source === "dndbeyond" && (
              <p class="small muted">{w.typedReplacesLink}</p>
            )}
            <button type="submit" class="button" aria-disabled={busy}>
              {w.typedSave}
            </button>
          </form>
        </details>
      )}
    </div>
  );
}

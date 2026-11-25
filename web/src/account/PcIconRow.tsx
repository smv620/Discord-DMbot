/** @jsxImportSource preact */
// The Icon row on a player character's details panel (#3622): Use Discord, Use dndbeyond.com, Remove and Hide the icon,
// with one Undo. Offered only to the DM and to the player who plays the character (the door says which through
// `canSetIcon`, and checks again); a button is offered only when it would change something, and a picture only when it
// exists. Its own file so the Story Graph's page keeps its weight budget.
import { useEffect, useRef, useState } from "preact/hooks";

import { pcIconWords as w } from "../content/pc-icon-words";
import type { AccountApi, IconChoice, PictureItem, WebNode } from "./api";
import SavingWait from "./SavingWait";

/** The numeric id of a kept picture: the second part of its gallery cursor "made-at:id" (core #5280 takes this id, never the code). */
export const pictureId = (p: PictureItem): number => Number(p.cursor.split(":")[1]);
const hasId = (p: PictureItem): boolean => Number.isInteger(pictureId(p)); // (a malformed cursor is never offered)

/** Pictures the person may pick: never one read out of a DM's file, and a player only their own (core re-checks both). */
export const pickable = (list: PictureItem[], isDm: boolean): PictureItem[] => list.filter((p) => hasId(p) && !p.dmOnly && (isDm || p.mine));

export default function PcIconRow({
  node,
  api,
  refId,
  onChoose,
  undo,
  onUndo,
}: {
  node: WebNode;
  api?: AccountApi | undefined;
  refId?: string | undefined;
  /** Sends the choice (a picture's id goes with `custom`); resolves to a plain-words problem, or null when it was saved. */
  onChoose: (choice: IconChoice, picture?: number) => Promise<string | null>;
  /** Set when the last change here can be taken back. */
  undo: boolean;
  onUndo: () => Promise<string | null>;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [picking, setPicking] = useState(false);
  const [shown, setShown] = useState<PictureItem[] | null>(null);
  const [isDm, setIsDm] = useState(false);
  const [more, setMore] = useState(false);
  const [looking, setLooking] = useState(false);
  const [lookProblem, setLookProblem] = useState(false);
  async function look(before?: string): Promise<void> {
    if (!api?.pictures || !refId || looking) return;
    setLooking(true);
    setLookProblem(false);
    try {
      const got = await api.pictures(refId, before ? { before } : {});
      setIsDm(got.isDm);
      setMore(got.more);
      setShown((now) => [...(before ? (now ?? []) : []), ...got.pictures]);
    } catch {
      setLookProblem(true);
    } finally {
      setLooking(false);
    }
  }
  useEffect(() => {
    if (picking) void look(); // (read again at each opening, so a picture removed since is not offered)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picking]);
  useEffect(() => {
    // A player may get a page with none of their own: keep reading until one shows or the pages end.
    if (picking && shown && more && !looking && !lookProblem && pickable(shown, isDm).length === 0) void look(shown[shown.length - 1]?.cursor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, more, looking, picking]);
  const statusBox = useRef<HTMLParagraphElement>(null); // a pressed button may leave the row: focus goes to what was said
  const choice = node.iconChoice ?? "default";
  const has = node.iconHas ?? { discord: false, dndbeyond: false };
  const canPickPictures = Boolean(has.pictures && api?.pictures && refId);
  async function go(run: () => Promise<string | null>, ok: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaid("");
    try {
      const refused = await run();
      if (refused) setProblem(refused);
      else {
        setSaid(ok);
        setTimeout(() => statusBox.current?.focus(), 0);
      }
    } finally {
      setBusy(false);
    }
  }
  const pick = (to: IconChoice) => () => void go(() => onChoose(to), w.done);
  return (
    <div class="stack" role="group" aria-labelledby={`pc-icon-${node.id}`}>
      <h4 id={`pc-icon-${node.id}`}>{w.heading}</h4>
      <p class="small muted">{w.scope}</p>
      <p class="small muted" aria-live="polite">
        {w.showing[choice]}
      </p>
      <div class="row">
        {has.discord && choice !== "discord" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("discord")}>
            {w.useDiscord}
          </button>
        )}
        {has.dndbeyond && choice !== "dndbeyond" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("dndbeyond")}>
            {w.useBeyond}
          </button>
        )}
        {canPickPictures && (
          <button type="button" class="button secondary" aria-expanded={picking} aria-disabled={busy} onClick={() => setPicking((open) => !open)}>
            {picking ? w.closePicker : w.setPicture}
          </button>
        )}
        {choice !== "default" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("default")}>
            {w.remove}
          </button>
        )}
        {choice !== "hidden" && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={pick("hidden")}>
            {w.hide}
          </button>
        )}
        {undo && (
          <button type="button" class="button secondary" aria-label={w.undoLabel} aria-disabled={busy} onClick={() => void go(onUndo, w.undone)}>
            {w.undo}
          </button>
        )}
      </div>
      {picking && canPickPictures && (
        <div class="stack" role="group" aria-labelledby={`pc-pictures-${node.id}`}>
          <h5 id={`pc-pictures-${node.id}`}>{w.pickerHeading}</h5>
          {looking && <p class="small muted" role="status">{w.loading}</p>}
          {lookProblem && (
            <>
              <p class="warn small" role="alert">{w.lookFailed}</p>
              <button type="button" class="button secondary" onClick={() => void look()}>
                {w.tryAgain}
              </button>
            </>
          )}
          {shown !== null && !looking && pickable(shown, isDm).length === 0 && !more && <p class="small">{w.noPictures}</p>}
          <ul class="stack small" style={{ maxHeight: "22rem", overflowY: "auto" }}>
            {pickable(shown ?? [], isDm).map((p) => (
              <li key={p.code} class="row">
                <img src={p.previewUrl} alt={w.pictureAlt(p.description)} width={64} height={64} loading="lazy" />
                <button
                  type="button"
                  class="button secondary"
                  aria-disabled={busy}
                  aria-label={p.description ? w.usePictureNamed(p.description) : undefined}
                  onClick={() => void go(async () => { const refused = await onChoose("custom", pictureId(p)); if (!refused) setPicking(false); return refused; }, w.done)}
                >
                  {w.usePicture}
                </button>
              </li>
            ))}
          </ul>
          {more && !looking && shown && (
            <button type="button" class="button secondary" onClick={() => void look(shown[shown.length - 1]?.cursor)}>
              {w.more}
            </button>
          )}
        </div>
      )}
      {choice !== "default" && <p class="small muted">{w.removeHint}</p>}
      <SavingWait active={busy} />
      <p class="small" role="status" tabIndex={-1} ref={statusBox}>
        {said}
      </p>
      {problem && (
        <p class="warn small" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

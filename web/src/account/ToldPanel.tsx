/** @jsxImportSource preact */
// "Tavin now knows the guard is Ken" and Take back, in the Story Graph's name panel (#3542, website half of #3089).
// For each secret other name of this entry: a choice for every character who holds it and was not told, and a line
// with Take back (asked once) for every character who was. The bot applies each press (`told-add`, `told-remove`);
// the list is read again afterwards. DM only, and only while TOLD_LIVE (the Story Graph decides both). One press at a
// time. Names and ids only: no word of the story.
import { useEffect, useRef, useState } from "preact/hooks";

import { toldWords as w } from "../content/told-words";
import { ApiError, type AccountApi, type ToldName } from "./api";
import SavingWait from "./SavingWait";

type Who = { id: string; name: string };

export default function ToldPanel({ api, campaignRef, entryId, entryName }: { api: AccountApi; campaignRef: string; entryId: string; entryName: string }) {
  const [names, setNames] = useState<ToldName[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const flight = useRef(false);
  // Signed out: the page's own next call sends the person to sign in; here it reads as the failure it is.

  async function read(): Promise<void> {
    if (!api.toldPanel) return;
    try {
      setNames(await api.toldPanel(campaignRef));
      setFailed(false);
    } catch (error) {
      setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function press(add: boolean, secret: ToldName, who: Who): Promise<void> {
    if (flight.current || !api.changeName) return;
    flight.current = true;
    setBusy(true);
    setProblem(null);
    setSaid("");
    setAsking(null);
    try {
      await api.changeName(campaignRef, entryId, { action: add ? "told-add" : "told-remove", alias: secret.id, character: who.id });
      setSaid(add ? w.saidTold(who.name, secret.name, entryName) : w.saidBack(who.name, secret.name, entryName));
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
    } finally {
      flight.current = false;
      setBusy(false);
    }
    await read(); // (the change is already made: a failed read must not read as a failed change)
  }

  if (failed) return <p class="warn small" role="alert">{w.loadFailed}</p>;
  const mine = (names ?? []).filter((n) => n.entryId === entryId && (n.holders.length > 0 || n.told.length > 0));
  // Nothing to tell and nothing told: no heading and no empty box (a said line from a press still shows).
  if (mine.length === 0 && !said && !problem) return null;
  return (
    <div class="stack told-panel">
      <strong>{w.heading}</strong>
      <p class="small">{w.lead}</p>
      {mine.map((secret) => (
        <ul key={secret.id} class="stack plain-list">
          {secret.told.map((who) => (
            <li key={`t${who.id}`} class="stack">
              <p>{w.told(who.name, secret.name, entryName)}</p>
              {asking === `${secret.id}:${who.id}` ? (
                <div class="stack" role="group" aria-label={w.takeBackFor(who.name, secret.name)}>
                  <p role="status">{w.sure(who.name, secret.name, entryName)}</p>
                  <div class="row">
                    <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void press(false, secret, who)}>
                      {w.yesTakeBack}
                    </button>
                    <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && setAsking(null)}>
                      {w.keep}
                    </button>
                  </div>
                </div>
              ) : (
                <p>
                  <button type="button" class="button secondary" aria-disabled={busy} aria-label={w.takeBackFor(who.name, secret.name)} onClick={() => !busy && setAsking(`${secret.id}:${who.id}`)}>
                    {w.takeBack}
                  </button>
                </p>
              )}
            </li>
          ))}
          {secret.holders.map((who) => (
            <li key={`h${who.id}`}>
              <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void press(true, secret, who)}>
                {w.tell(who.name, secret.name, entryName)}
              </button>
            </li>
          ))}
        </ul>
      ))}
      {/* Always there, so a screen reader hears what is put in it. */}
      <SavingWait active={busy} />
      <p class="small" role="status">{said}</p>
      {problem && <p class="warn small" role="alert">{problem}</p>}
    </div>
  );
}

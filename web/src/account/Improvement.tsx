/** @jsxImportSource preact */
// The My Account switch "Use what I say and type to improve DMbot" (#1610). Shown only when the API
// has the route (an older API shows nothing). It saves when pressed and says so in one line.
import { useEffect, useRef, useState } from "preact/hooks";

import { text } from "../content/improvement";
import { ApiError, type AccountApi } from "./api";

export function Improvement({ api }: { api: AccountApi }) {
  // null: not known yet, or this API has no such route.
  const [on, setOn] = useState<boolean | null>(null);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);
  const saving = useRef(false);
  const has = !!api.improvement && !!api.setImprovement;

  useEffect(() => {
    if (!has || !api.improvement) return;
    let live = true;
    api.improvement().then(
      (found) => live && setOn(found),
      () => undefined, // no route, or not signed in: show nothing
    );
    return () => {
      live = false;
    };
  }, [api, has]);

  if (!has || on === null) return null;
  const { setImprovement } = api;

  const change = (to: boolean) => {
    if (saving.current || !setImprovement) return;
    saving.current = true;
    setNote({ ok: true, message: text.saving });
    setImprovement(to).then(
      (saved) => {
        saving.current = false;
        setOn(saved);
        setNote({ ok: true, message: saved ? text.savedOn : text.savedOff });
      },
      (error: unknown) => {
        saving.current = false;
        const signedOut = error instanceof ApiError && error.kind === "signed-out";
        setNote({ ok: false, message: signedOut ? text.signedOut : text.failed });
      },
    );
  };

  return (
    <section class="panel" aria-labelledby="improvement-heading">
      <h3 id="improvement-heading">{text.heading}</h3>
      <p>
        <label>
          <input
            type="checkbox"
            checked={on}
            onChange={(e) => change((e.currentTarget as HTMLInputElement).checked)}
          />{" "}
          {text.label}
        </label>
      </p>
      <p class="small muted">{text.note}</p>
      <p class={note && !note.ok ? "small warn" : "small"} role="status">
        {note?.message ?? ""}
      </p>
    </section>
  );
}

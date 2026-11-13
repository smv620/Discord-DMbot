/** @jsxImportSource preact */
// The "who plays it" picker on the Names page (#4225): the server's members by name, the DM's players first and marked, and a last row,
// "Someone else: type their Discord ID…", for a person who is not in the list. No Discord id is shown or typed unless the DM asks for that
// row. In a file of its own (loaded when a player character is being edited) so the Names page keeps its size.
import { useEffect, useState } from "preact/hooks";

import { namesPage as words } from "../content/names-words";
import type { AccountApi } from "./api";
import PlayerPicker from "./PlayerPicker";
import type { WizardMember } from "./wizard-api";

interface Props {
  api: AccountApi;
  refId: string;
  value: string;
  onPick(id: string): void;
  onElsewhere(): void;
  /** Called when the members could not be loaded, so the page falls back to typing the id. */
  onFailed(): void;
}

export default function NamesPlayerField({ api, refId, value, onPick, onElsewhere, onFailed }: Props) {
  const [members, setMembers] = useState<WizardMember[] | null>(null);
  useEffect(() => {
    let live = true;
    const load = api.importMembers;
    if (!load) return void onFailed();
    load
      .call(api, refId, "")
      .then((got) => {
        if (live) setMembers(got.map((m) => ({ userId: m.userId, name: m.username && m.username !== m.name ? `${m.name} (${m.username})` : m.name, before: m.before === true })));
      })
      .catch(() => live && onFailed());
    return () => {
      live = false;
    };
  }, [api, refId]);
  if (members === null) return <p class="muted" role="status">…</p>;
  return <PlayerPicker label={words.playerPickLabel} members={members} value={value === "" ? "pending" : value} onPick={(id) => onPick(id === "pending" ? "" : id)} elsewhere={{ label: words.playerElsewhere, onChoose: onElsewhere }} />;
}

/** @jsxImportSource preact */
// DMbot's summary of the scene a question came from (#3644, website half of #3596), above the answers and above "Where was
// it said?". Plain text, never HTML, wrapping at any width; nothing at all (no empty box) when there is none or while its
// switch is off. The door only ever sends it to a DM of the campaign.
import { SCENES_LIVE } from "../content/scene-switch";
import { sceneWords as w } from "../content/scene-words";

export default function SceneLine({ scene }: { scene?: string | null | undefined }) {
  const text = scene?.trim();
  if (!SCENES_LIVE || !text) return null;
  return (
    <p class="small scene-line">
      <strong>{w.label}</strong> {text}
    </p>
  );
}

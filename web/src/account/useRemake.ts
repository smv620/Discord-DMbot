// What making a recap's audio again would do now (#2630), asked from one place so the audio part and the
// Approve buttons show the same answer. Asked when the audio is made from older text (a draft the DM has
// just edited, where Approve queues the new audio at once, or a posted recap), or when the DM could press
// Make the audio again. The last answer stays while a new one is fetched, so words and button don't
// flicker; a failed or missing lookup leaves the old words.
import { useEffect, useState } from "preact/hooks";

import { RECAP_AUDIO_LIVE, RECAP_REMAKE_LIVE } from "../content/recap-words";
import type { AccountApi, Recap, RecapRemake } from "./api";

export const remakeOn = (): boolean => RECAP_REMAKE_LIVE && RECAP_AUDIO_LIVE;

/** The audio was made from text that has changed since. */
export const audioIsStale = (recap: Recap): boolean =>
  recap.audio?.state === "ready" && !recap.audio.current;

export function useRemake(api: AccountApi, recap: Recap): RecapRemake | null {
  const [remake, setRemake] = useState<RecapRemake | null>(null);
  const audio = recap.audio;
  const eligible = remakeOn() && !!audio && (audioIsStale(recap) || audio.canAskAgain);
  useEffect(() => {
    if (!eligible || !api.recapAudioRemake) {
      setRemake(null);
      return;
    }
    let live = true;
    api.recapAudioRemake(recap.ref).then(
      (found) => live && setRemake(found),
      () => live && setRemake(null), // (a failed look leaves the old button and words)
    );
    return () => {
      live = false;
    };
  }, [api, recap.ref, eligible, recap.summary, audio?.current, audio?.state]);
  return remake;
}

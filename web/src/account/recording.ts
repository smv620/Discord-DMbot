// What the browser can record, shared by the "Speak instead" note (#2114) and "How to say it" (#2317).

/** The first recording format this browser makes that the API reads (webm or ogg), or null. */
export const pickType = (): string | null => {
  if (typeof MediaRecorder === "undefined") return null;
  for (const type of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"])
    if (MediaRecorder.isTypeSupported(type)) return type;
  return null;
};

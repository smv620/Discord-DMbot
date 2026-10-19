/** @jsxImportSource preact */
// A recap's text with its cues as tags, for the game master's page only (#2017): 《a mood》 is a
// coloured tag and ((a pause or pace)) a grey one, so they can see where the music changes. The
// words DMbot posts to the players have the cues taken out, so this never feeds the posted text.

// The same shapes as core/src/dmbot/recaps/text.py.
const CUE = /(《[^《》]{1,60}》|\(\([^()]{1,60}\)\))/g;

/** The same mood always gets the same colour, as in the test review page. */
export function cueHue(name: string): number {
  let sum = 0;
  [...name.toLowerCase()].forEach((c, i) => {
    sum += (c.codePointAt(0) ?? 0) * (i + 3);
  });
  return sum % 360;
}

export function hasCues(text: string): boolean {
  return new RegExp(CUE.source).test(text);
}

function Line({ text }: { text: string }) {
  return (
    <>
      {text.split(CUE).map((part, i) => {
        if (part.startsWith("《") && part.endsWith("》")) {
          return (
            <span
              key={i}
              class="cue mood"
              style={{ "--hue": String(cueHue(part.slice(1, -1))) }}
            >
              {part}
            </span>
          );
        }
        if (part.startsWith("((") && part.endsWith("))")) {
          return (
            <span key={i} class="cue pace">
              {part}
            </span>
          );
        }
        return part;
      })}
    </>
  );
}

export default function CuedText({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n{2,}/).map((paragraph, i) => (
        <p key={i}>
          <Line text={paragraph} />
        </p>
      ))}
    </>
  );
}

// Turns the first-feeling reminder's Discord marks into plain parts (#5000): `**name**` is bold, and a backslash before one of
// the marks is an escape for that mark. Only bold is supported (no nesting); the other marks are escapable but not formatted,
// as Discord's own reminder uses them. The result is text parts only, never HTML, so the page shows whatever the words say.
export interface FeelingPart {
  text: string;
  bold: boolean;
}

const ESCAPABLE = new Set(["\\", "*", "_", "~", "`", "|", ">"]);

/** Whether an unescaped `**` comes after `from`: an escaped `\*` before it does not close anything. */
function hasCloser(source: string, from: number): boolean {
  for (let j = from; j < source.length - 1; j += 1) {
    if (source[j] === "\\" && ESCAPABLE.has(source[j + 1]!)) {
      j += 1;
    } else if (source[j] === "*" && source[j + 1] === "*") {
      return true;
    }
  }
  return false;
}

export function feelingParts(source: string): FeelingPart[] {
  const parts: FeelingPart[] = [];
  let bold = false;
  let buf = "";
  const flush = () => {
    if (buf !== "") parts.push({ text: buf, bold });
    buf = "";
  };
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i]!;
    if (ch === "\\" && i + 1 < source.length && ESCAPABLE.has(source[i + 1]!)) {
      buf += source[i + 1]!;
      i += 1;
    } else if (ch === "*" && source[i + 1] === "*") {
      // A closing `**` ends the bold part; an opening one needs an unescaped closing one later, or it is kept as it is.
      if (bold || hasCloser(source, i + 2)) {
        flush();
        bold = !bold;
        i += 1;
      } else {
        buf += "**";
        i += 1;
      }
    } else {
      buf += ch;
    }
  }
  flush();
  return parts;
}

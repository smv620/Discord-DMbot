/** @jsxImportSource preact */
// Shows the bot's card text (Discord's markdown: **bold**, *italic* or _italic_, `code`) as elements, never as HTML: every
// other character is plain text, so a "<b>" in the card shows as written. An unclosed marker stays literal, and an
// underscore inside a word (snake_case_name) is not italic.
export type Piece = { text: string; kind: "plain" | "bold" | "italic" | "code" };

const MARK = /\*\*([^*\n]+?)\*\*|`([^`\n]+)`|\*([^*\n]+?)\*|(?<!\w)_([^_\n]+?)_(?!\w)/g;

/** The text cut into plain, bold, italic and code pieces, in order. */
export function discordPieces(text: string): Piece[] {
  const pieces: Piece[] = [];
  let last = 0;
  for (const m of text.matchAll(MARK)) {
    const at = m.index ?? 0;
    if (at > last) pieces.push({ text: text.slice(last, at), kind: "plain" });
    if (m[1] !== undefined) pieces.push({ text: m[1], kind: "bold" });
    else if (m[2] !== undefined) pieces.push({ text: m[2], kind: "code" });
    else pieces.push({ text: (m[3] ?? m[4]) as string, kind: "italic" });
    last = at + m[0].length;
  }
  if (last < text.length) pieces.push({ text: text.slice(last), kind: "plain" });
  return pieces;
}

export default function DiscordText({ text }: { text: string }) {
  return (
    <>
      {discordPieces(text).map((p, i) =>
        p.kind === "bold" ? <strong key={i}>{p.text}</strong> : p.kind === "italic" ? <em key={i}>{p.text}</em> : p.kind === "code" ? <code key={i}>{p.text}</code> : p.text,
      )}
    </>
  );
}

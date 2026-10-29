export const copiedText: string;
export const couldNotCopy: string;
export function linkTo(loc: { origin: string; pathname: string }, id: string): string;
export function wireCopy(
  root: ParentNode,
  status: HTMLElement,
  env: { clipboard: { writeText(text: string): Promise<void> }; location: { origin: string; pathname: string; hash: string } },
  wait?: number,
): void;

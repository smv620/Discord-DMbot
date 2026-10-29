export interface Phrase {
  phrase: string;
  rest: string;
}
export function labelsInPython(text: string): string[];
export function plain(s: string): string;
export function unmark(text: string): string;
export function buttonPhrases(text: string): Phrase[];
export function closestLabel(phrase: string, labels: Iterable<string>): string;
export function labelProblems(items: Phrase[], labels: Iterable<string>, allowed?: string[]): (Phrase & { closest: string })[];
export const pendingBotLabels: { label: string; issue: number }[];
export const hiddenBotLabels: { label: string; file: string }[];
export const siteButtons: string[];
export const discordControls: string[];

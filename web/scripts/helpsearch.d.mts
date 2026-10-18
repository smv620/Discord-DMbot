export function normalize(text: string): string;
export function matches(query: string, text: string): boolean;
export const nothingFound: string;
export function statusText(query: string, found: number): string;
export function applySearch(root: ParentNode, query: string): number;
export function wire(form: HTMLFormElement, page: ParentNode, wait?: number): (now?: boolean) => void;

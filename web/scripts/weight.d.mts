export const KB: number;
export interface Budget {
  htmlKB: number;
  totalKB: number;
}
export interface Budgets extends Budget {
  imageKB: number;
  /** The most a chunk loaded on demand with import() may weigh (#2690); no check when missing. */
  onDemandKB?: number;
  pages?: Record<string, Budget>;
}
export interface PageWeight {
  page: string;
  htmlBytes: number;
  files: Map<string, number>;
  alternatives: Set<string>;
  /** Chunks loaded only when asked for, with their sizes: not part of `totalBytes`. */
  onDemand: Map<string, number>;
  totalBytes: number;
}
export function filesUnder(dir: string): string[];
export function localReferences(text: string, kind: "html" | "css" | "js"): string[];
export function onDemandReferences(text: string): string[];
export function weighPage(dist: string, page: string): PageWeight;
export function checkBuild(
  dist: string,
  budgets: Budgets,
): { rows: PageWeight[]; images: { file: string; bytes: number; pages: string[] }[]; fonts: string[]; problems: string[] };
export function table(rows: PageWeight[], budgets: Budgets): string;

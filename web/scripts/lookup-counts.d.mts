export const kindNames: Record<string, string>;
export interface RulesFile {
  kind: string;
  entries: { kind?: string }[];
}
export function readFolder(dir: string): RulesFile[];
export function countByKind(files: RulesFile[]): Map<string, number>;
export function unnamedKinds(files: RulesFile[]): string[];
export function lookupList(files: RulesFile[]): { label: string; count: number }[];

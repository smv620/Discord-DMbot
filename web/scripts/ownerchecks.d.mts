export function ownerWaiting<T extends { title: string; reason: string }>(held: T[]): T[];
export function readChecks(doc: string): { unlocks: Set<string>; explained: Set<string> };
export function ownerCheckProblems(held: { title: string; reason: string }[], doc: string): string[];
export function unclassified<T extends { title: string; reason: string }>(held: T[]): T[];

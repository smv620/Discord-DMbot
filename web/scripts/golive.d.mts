export function switchesInCode(contentDir: string): string[];
export function switchesInDoc(doc: string): string[];
export function docRows(doc: string): { name: string; deploy: string }[];
export function goLiveProblems(codeSwitches: string[], doc: string): string[];

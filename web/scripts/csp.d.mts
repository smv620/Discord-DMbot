export function htmlFiles(dir: string): string[];
export function inlineScriptHashes(html: string): string[];
export function apiOrigin(base: string | undefined): string | null;
export const MOCK_MARKER: string;
export function mockLeaks(dist: string): string[];
export function effectiveApiBase(env: Record<string, string | undefined>): string | undefined;
export function isDevSite(env: Record<string, string | undefined>): boolean;
export function addDevHeaders(headers: string, env: Record<string, string | undefined>): string;

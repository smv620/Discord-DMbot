// Parse rendered HTML into a DOM for the tests (the same happy-dom the account tests use).
import { Window } from "happy-dom";

export function parsePage(html: string): Document {
  const window = new Window();
  return new window.DOMParser().parseFromString(html, "text/html") as unknown as Document;
}

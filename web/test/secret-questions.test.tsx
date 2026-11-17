/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The 🤫 Known or secret? question on the Questions page (#4598): one question at a time in the API's words, Keep or Make queued
// for the bot, Undo after a make until the next answer, and each failure said plainly. Run against the pretend API.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, NAMES_METHODS, type SecretQuestion } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SecretQuestions from "../src/account/SecretQuestions";
import { secretWords as w } from "../src/content/secret-questions-words";

afterEach(cleanup);

let n = 0;
const narrow: SecretQuestion = {
  id: "q1",
  kind: "narrow",
  heading: "Bob Tamsin is an ally of Ilsa sounds like a secret, but your players already know it.",
  source: `From "Tavin's tale, 3 Nov".`,
  keep: { label: "Keep it known", says: "Keep it known: nothing changes, and DMbot won't ask this again.", done: "Left as known. DMbot won't ask about this again." },
  make: { label: "Make it secret", says: "Make it secret: this connection is hidden from your players in DMbot. Undo takes it back.", done: "Done: Bob Tamsin is an ally of Ilsa is a secret now. Undo takes it back." },
};
const reveal: SecretQuestion = {
  id: "q2",
  kind: "reveal",
  heading: "Your players know the name Old Bob. Only you know it is Bob Tamsin.",
  source: "",
  keep: { label: "Keep it secret", says: "Keep it secret: they don't learn it is Bob Tamsin, and DMbot won't ask this again.", done: "Kept secret. DMbot won't ask about this again." },
  make: { label: "Let them know", says: "Let them know: your players learn that Old Bob is Bob Tamsin. Undo takes it back.", done: "Done: your players now know Old Bob is Bob Tamsin. Undo takes it back." },
};

/** A page whose questions are the given list; an answer takes its question off, as the bot does. */
function withQuestions(list: SecretQuestion[]) {
  const api = mockApi("table");
  let waiting = structuredClone(list);
  api.secretQuestions = async () => structuredClone(waiting);
  api.answerSecret = async (ref, change) => {
    api.calls.push(`answerSecret:${ref}:${change.action}:${change.action === "undo" ? change.batch : change.question}`);
    if (change.action !== "undo") waiting = waiting.filter((q) => q.id !== change.question);
    return { batch: change.action === "make" ? 41 : null };
  };
  const ref = `100.cmp${(n += 1)}.secrets`;
  render(<SecretQuestions api={api} campaignRef={ref} id="s" />);
  return { api, ref };
}

describe("the 🤫 Known or secret? question", () => {
  it("shows the first question's words and both answers", async () => {
    withQuestions([narrow, reveal]);
    expect(await screen.findByText(narrow.heading)).toBeTruthy();
    expect(screen.getByText(narrow.source)).toBeTruthy();
    expect(screen.getByText(narrow.keep.says)).toBeTruthy();
    expect(screen.getByText(narrow.make.says)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Keep it known" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Make it secret" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: `${w.heading} (${w.waiting(2)})` })).toBeTruthy();
  });

  it("Keep it known sends keep for that question and says its done line", async () => {
    const { api, ref } = withQuestions([narrow, reveal]);
    fireEvent.click(await screen.findByRole("button", { name: "Keep it known" }));
    expect(await screen.findByText(narrow.keep.done)).toBeTruthy();
    expect(api.calls).toContain(`answerSecret:${ref}:keep:q1`);
    await waitFor(() => expect(screen.queryByText(narrow.heading)).toBeNull());
  });

  it("Make it secret sends make, says its done line and offers Undo", async () => {
    const { api, ref } = withQuestions([narrow]);
    fireEvent.click(await screen.findByRole("button", { name: "Make it secret" }));
    expect(await screen.findByText(narrow.make.done)).toBeTruthy();
    expect(api.calls).toContain(`answerSecret:${ref}:make:q1`);
    expect(screen.getByRole("button", { name: w.undo })).toBeTruthy();
  });

  it("Undo sends undo with the batch and says it is undone", async () => {
    const { api, ref } = withQuestions([narrow]);
    fireEvent.click(await screen.findByRole("button", { name: "Make it secret" }));
    fireEvent.click(await screen.findByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(api.calls).toContain(`answerSecret:${ref}:undo:41`);
  });

  it("a reveal question's buttons are Keep it secret and Let them know", async () => {
    withQuestions([reveal]);
    expect(await screen.findByRole("button", { name: "Keep it secret" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Let them know" })).toBeTruthy();
  });

  it("more than one waiting says how many", async () => {
    withQuestions([narrow, reveal]);
    expect(await screen.findByRole("heading", { name: `${w.heading} (${w.waiting(2)})` })).toBeTruthy();
  });

  it("an answered-meanwhile question says it was already answered and shows what is left", async () => {
    const api = withQuestions([narrow, reveal]).api;
    api.answerSecret = async () => {
      // The bot found the question answered meanwhile: it is no longer waiting, so the list no longer has it.
      api.secretQuestions = async () => [structuredClone(reveal)];
      throw Object.assign(new ApiError("gone"), { note: "gone" });
    };
    fireEvent.click(await screen.findByRole("button", { name: "Keep it known" }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.gone);
    expect(await screen.findByText(reveal.heading)).toBeTruthy();
  });

  it("a failed Undo says it is too late", async () => {
    const api = withQuestions([narrow]).api;
    fireEvent.click(await screen.findByRole("button", { name: "Make it secret" }));
    await screen.findByRole("button", { name: w.undo });
    api.answerSecret = async () => {
      throw Object.assign(new ApiError("server"), { note: "changed" });
    };
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.tooLate);
  });

  it("an empty list says nothing is left", async () => {
    withQuestions([]);
    expect(await screen.findByText(w.empty)).toBeTruthy();
  });

  it("the calls are named for the area, and the switch stays off", () => {
    expect(NAMES_METHODS).toContain("secretQuestions");
    expect(NAMES_METHODS).toContain("answerSecret");
  });
});

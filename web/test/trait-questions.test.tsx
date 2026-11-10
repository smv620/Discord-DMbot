/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The lineage and class question on the Questions page (#3946): one question at a time in Discord's words, Yes or No queued for
// the bot, the list read again after each answer, "already answered" and a failed save said plainly, and nothing left. Run against
// the pretend API (a campaign ref ending `.traits` has one lineage and one class question).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import TraitQuestions from "../src/account/TraitQuestions";
import { questionOf, traitWords as w } from "../src/content/trait-questions-words";
import { ApiError, type TraitOffer } from "../src/account/api";

afterEach(cleanup);

let n = 0;
const open = (ending = ".traits") => {
  const api = mockApi("table");
  const ref = `100.cmp${(n += 1)}${ending}`;
  render(<TraitQuestions api={api} campaignRef={ref} id="q" />);
  return { api, ref };
};

describe("the lineage and class question", () => {
  it("asks the first question in Discord's words: the question, where the lore said it, why DMbot asks", async () => {
    open();
    expect(await screen.findByText("Make Torvin a dwarf?")).toBeTruthy();
    expect(screen.getByText("DMbot has Torvin as a human.", { exact: false })).toBeTruthy();
    expect(screen.getByText(`Your lore "Notes, 9 Nov" says so:`)).toBeTruthy();
    expect(screen.getByText(/Torvin Ashgrove is a dwarf of the northern holds/)).toBeTruthy();
    expect(screen.getByText(w.why)).toBeTruthy();
    expect(screen.getByRole("heading", { name: `${w.heading} (${w.waiting(2)})` })).toBeTruthy();
  });

  it("a yes says what was done, and the next question comes up", async () => {
    const { api, ref } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect(await screen.findByText(w.kept("Torvin", "dwarf", true))).toBeTruthy();
    expect(await screen.findByText("Add cleric to Mira Voss's classes?")).toBeTruthy();
    expect(api.calls).toContain(`answerTrait:${ref}:species:dwarf:keep`);
    expect(screen.getByText("Your lore says so.")).toBeTruthy(); // (this one has no upload name and no quote)
  });

  it("a no says it was left, and the same question is not asked again", async () => {
    const { api, ref } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    expect(await screen.findByText(w.left("Torvin", "dwarf"))).toBeTruthy();
    expect(api.calls).toContain(`answerTrait:${ref}:species:dwarf:never`);
    expect(screen.queryByText("Make Torvin a dwarf?")).toBeNull();
  });

  it("says nothing is left after the last answer", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    await screen.findByText("Add cleric to Mira Voss's classes?"); // (the first is answered and the list read again)
    await waitFor(() => expect((screen.getByRole("button", { name: w.no }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: w.no }));
    expect(await screen.findByText(w.nothingLeft)).toBeTruthy();
  });

  it("a question answered meanwhile says so and shows what is left", async () => {
    open(".traits-gone");
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.gone);
    expect(await screen.findByText("Add cleric to Mira Voss's classes?")).toBeTruthy();
  });

  it("a failed save says so and the buttons work again", async () => {
    open(".traits-fail");
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNot);
    await waitFor(() => expect((screen.getByRole("button", { name: w.yes }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("pressing Yes twice sends one answer", async () => {
    const { api } = open();
    const yes = await screen.findByRole("button", { name: w.yes });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await screen.findByText(w.kept("Torvin", "dwarf", true));
    expect(api.calls.filter((c) => c.startsWith("answerTrait:")).length).toBe(1);
  });

  it("an answer the bot has not finished says it is still working", async () => {
    const api = mockApi("table");
    api.answerTrait = async () => Promise.reject(new ApiError("still-waiting"));
    render(<TraitQuestions api={api} campaignRef={`100.cmp${(n += 1)}.traits`} id="q" />);
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.stillWorking);
  });

  it("a campaign with none says nothing is left", async () => {
    open(".none");
    expect(await screen.findByText(w.nothingLeft)).toBeTruthy();
  });
});

describe("the question's words", () => {
  const base: TraitOffer = { entity: "e", who: "Ilya", kind: "species", name: "Elf", key: "elf", known: true, edition: null, current: null, classes: [], upload: "", quote: "" };
  it("says an before a vowel, and 'no lineage yet' for a first one", () => {
    const q = questionOf(base);
    expect(q.ask).toBe("Make Ilya an elf?");
    expect(q.has).toBe("DMbot has no lineage for Ilya yet.");
    expect(q.yes).toBe("Ilya becomes an elf.");
    expect(q.no).toBe("nothing changes");
  });
  it("a class adds to the ones they have", () => {
    const q = questionOf({ ...base, kind: "class", name: "Cleric", classes: ["Fighter"] });
    expect(q.ask).toBe("Add cleric to Ilya's classes?");
    expect(q.has).toBe("DMbot has Ilya as fighter.");
    expect(q.yes).toBe("cleric is added to Ilya's classes; the ones they have stay.");
  });
});

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
import { ApiError, httpApi, type TraitOffer } from "../src/account/api";

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
    expect(screen.getByRole("heading", { name: `${w.heading} (${w.waiting(3)})` })).toBeTruthy();
  });

  it("a yes says what was done, and the next question comes up", async () => {
    const { api, ref } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect(await screen.findByText(w.kept("Torvin", "dwarf", "species"))).toBeTruthy();
    expect(await screen.findByText("Add cleric to Mira Voss's classes?")).toBeTruthy();
    expect(api.calls).toContain(`answerTrait:${ref}:species:dwarf:keep`);
    expect(screen.getByText("Your lore says so.")).toBeTruthy(); // (this one has no upload name and no quote)
  });

  it("a no says it was left, and the same question is not asked again", async () => {
    const { api, ref } = open();
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    expect(await screen.findByText(w.left("Torvin", "dwarf", "species"))).toBeTruthy();
    expect(api.calls).toContain(`answerTrait:${ref}:species:dwarf:never`);
    expect(screen.queryByText("Make Torvin a dwarf?")).toBeNull();
  });

  it("says nothing is left after the last answer", async () => {
    open();
    for (const next of ["Add cleric to Mira Voss's classes?", "Does Brewner speak Giant?"]) {
      fireEvent.click(await screen.findByRole("button", { name: w.no }));
      await screen.findByText(next); // (each answer is read again before the next question shows)
      await waitFor(() => expect((screen.getByRole("button", { name: w.no }) as HTMLButtonElement).disabled).toBe(false));
    }
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

  it.each([
    [".traits-refused", w.refused("Torvin")],
    [".traits-notallowed", w.notAllowed],
    [".traits-lost", w.couldNot],
    [".traits-gone", w.gone],
    [".traits-fail", w.couldNot],
  ])("an answer that ends %s says its plain line", async (ending, line) => {
    open(ending);
    fireEvent.click(await screen.findByRole("button", { name: w.yes }));
    expect((await screen.findByRole("alert")).textContent).toBe(line);
  });

  it("pressing Yes twice sends one answer", async () => {
    const { api } = open();
    const yes = await screen.findByRole("button", { name: w.yes });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await screen.findByText(w.kept("Torvin", "dwarf", "species"));
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

const brewner: TraitOffer = {
  entity: "e3", who: "Brewner", kind: "language", name: "Giant", key: "giant", known: true, edition: "2024", current: null, classes: [],
  upload: "Session 4 notes", quote: "Brewner speaks Giant, the tongue of the hill clans.",
};
/** A page whose only question is the one given (the mock's own list is not used for the read). */
function withOffers(list: TraitOffer[]) {
  const api = mockApi("table");
  api.traitOffers = async () => structuredClone(list);
  const ref = `100.cmp${(n += 1)}.traits`;
  render(<TraitQuestions api={api} campaignRef={ref} id="q" />);
  return { api, ref };
}

describe("the language question (#4632)", () => {
  it("asks Does {who} speak {name}? with its own heading, Yes, add it and No, leave it", async () => {
    withOffers([brewner]);
    expect(await screen.findByText("Does Brewner speak Giant?")).toBeTruthy();
    expect(screen.getByRole("heading", { name: w.languages })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.yesAdd })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.no })).toBeTruthy();
  });

  it("a language question has no DMbot-has line and its yes and no lines are the specified ones", async () => {
    withOffers([brewner]);
    await screen.findByText("Does Brewner speak Giant?");
    expect(screen.queryByText(/DMbot has/)).toBeNull();
    expect(screen.getByText(`${w.yesAdd}: Giant is added to Brewner's languages. ${w.no}: nothing changes, and DMbot won't ask this again.`)).toBeTruthy();
  });

  it("Yes on a language question sends kind language and key, and says Done: … speaks …", async () => {
    const { api, ref } = withOffers([brewner]);
    fireEvent.click(await screen.findByRole("button", { name: w.yesAdd }));
    expect(await screen.findByText(w.kept("Brewner", "Giant", "language"))).toBeTruthy();
    expect(api.calls).toContain(`answerTrait:${ref}:language:giant:keep`);
  });

  it("No on a language question says it will not be asked again and has no name card sentence", async () => {
    withOffers([brewner]);
    fireEvent.click(await screen.findByRole("button", { name: w.no }));
    expect(await screen.findByText(w.left("Brewner", "Giant", "language"))).toBeTruthy();
    expect(screen.queryByText(/name card/)).toBeNull();
  });

  it("the lore line ends with a colon and the quote when there is one, a full stop when not (all three kinds)", async () => {
    withOffers([brewner]);
    expect(await screen.findByText(`Your lore "Session 4 notes" says so:`)).toBeTruthy();
    expect(w.source("Notes", true)).toBe(`Your lore "Notes" says so:`);
    expect(w.source("Notes", false)).toBe(`Your lore "Notes" says so.`);
    expect(w.source("", false)).toBe("Your lore says so.");
  });

  it("the other two kinds keep their words, with Undo is in Discord on the done line", () => {
    expect(w.kept("Torvin", "dwarf", "species")).toBe("Done: Torvin is now a dwarf. Undo is in Discord.");
    expect(w.kept("Mira Voss", "cleric", "class")).toBe("Done: cleric added to Mira Voss. Undo is in Discord.");
    expect(w.left("Torvin", "dwarf", "species")).toMatch(/To change it later, open Torvin's name card\.$/);
  });

  it("refused and could-not words are the new ones", () => {
    expect(w.refused("Brewner")).toBe("DMbot couldn't change Brewner: they may have been joined with another name or removed. Showing what is left.");
    expect(w.couldNot).toBe("DMbot couldn't save that answer. Nothing changed. Try again in a minute.");
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

describe("the real client reads how an answer ended", () => {
  const net = (reads: unknown[]) => {
    const queue = [...reads];
    return httpApi("https://api.example", (async (_url: string, init: RequestInit) => {
      const body = init.method === "POST" ? { id: "a".repeat(32) } : (queue.length > 1 ? queue.shift() : queue[0]);
      return new Response(JSON.stringify(body), { status: init.method === "POST" ? 202 : 200, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch);
  };
  const offer = { entity: "e1", kind: "species" as const, key: "dwarf" };
  it("a done answer gives the change's number", async () => {
    const api = net([{ state: "waiting" }, { state: "done", batch: 7 }]);
    expect(await api.answerTrait!("100.cmp1", offer, "keep")).toEqual({ batch: 7 });
  });
  it.each(["gone", "refused", "not-allowed", "failed", "lost"])("a failed answer keeps the word %s", async (word) => {
    const api = net([{ state: "failed", error: word }]);
    await expect(api.answerTrait!("100.cmp1", offer, "never")).rejects.toMatchObject({ note: word });
  });
});

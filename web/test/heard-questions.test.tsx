/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Did they mean…?" on the Questions page (#4841 B1, #4857): the DM names the word as heard, keeps it, or puts it off with ⏳ Later.
// Tested on the card itself; the Questions page only mounts it while HEARD_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi, type HeardQuestion } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import HeardQuestionsCard from "../src/account/HeardQuestions";

afterEach(() => {
  cleanup();
});

const REF = "100.cmp1";
const wren: HeardQuestion = {
  id: "h1",
  heard: "Vorrun",
  speaker: "Wren",
  options: [
    { index: 0, id: "n1", name: "Vorun" },
    { index: 2, id: "n2", name: "Vorrin the Tall" },
  ],
};
const ash: HeardQuestion = { id: "h2", heard: "Sterling", speaker: null, options: [{ index: 1, id: "n3", name: "Sterling Vale" }] };

/** An API that holds `list`: a pick or a keep takes its question out, unless the test answers it itself. */
function fakeApi(list: HeardQuestion[], over: { pick?: () => Promise<{ code: string | null }>; keep?: () => Promise<{ code: string | null }> } = {}) {
  let questions = list.map((q) => structuredClone(q));
  const picks: [string, number][] = [];
  const keeps: string[] = [];
  const api = mockApi("table");
  api.heardQuestions = async () => ({ total: questions.length, questions: structuredClone(questions) });
  api.heardPick = async (_ref, question, index) => {
    picks.push([question, index]);
    if (over.pick) return over.pick();
    questions = questions.filter((q) => q.id !== question);
    return { code: "fixed" };
  };
  api.heardKeep = async (_ref, question) => {
    keeps.push(question);
    if (over.keep) return over.keep();
    questions = questions.filter((q) => q.id !== question);
    return { code: "kept" };
  };
  return { api, picks, keeps };
}

describe("Did they mean…? on the Questions page (#4857)", () => {
  it("says what was heard and who said it, and how many come after", async () => {
    const { api } = fakeApi([wren, ash]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    expect(await screen.findByText('DMbot heard Wren say "Vorrun". Did they mean…')).toBeTruthy();
    expect(screen.getByText("Not sure? Press ⏳ Later.")).toBeTruthy();
    expect(screen.getByText("1 more after this.")).toBeTruthy();
  });

  it("says a player when the speaker is not known", async () => {
    const { api } = fakeApi([ash]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    expect(await screen.findByText('DMbot heard a player say "Sterling". Did they mean…')).toBeTruthy();
    expect(screen.queryByText(/more after this/)).toBeNull();
  });

  it("cuts a name past 25 characters and a word past 18 on the buttons, as core does", async () => {
    const long: HeardQuestion = {
      id: "h3",
      heard: "abcdefghijklmnopqrstuvwxyzABCD",
      speaker: null,
      options: [{ index: 0, id: "n4", name: "Abcdefghijklmnopqrstuvwxyz0123456789ABCD" }],
    };
    const { api } = fakeApi([long]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    expect(await screen.findByRole("button", { name: "Abcdefghijklmnopqrstuvwx…" })).toBeTruthy();
    expect(screen.getByRole("button", { name: 'Keep "abcdefghijklmnopq…"' })).toBeTruthy();
  });

  it("a pick sends the option's own index, and says the name it is now written as", async () => {
    const { api, picks } = fakeApi([wren]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Vorrin the Tall" }));
    expect(await screen.findByText('✅ Got it: "Vorrun" is now written Vorrin the Tall, in that line and from now on in this campaign. Earlier lines stay as heard.')).toBeTruthy();
    expect(picks).toEqual([["h1", 2]]);
  });

  it("a pick that changes the line from now on says so in its own words", async () => {
    const { api } = fakeApi([wren], { pick: async () => ({ code: "line-kept" }) });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Vorrin the Tall" }));
    expect(
      await screen.findByText('✅ Got it: from now on, "Vorrun" is written Vorrin the Tall in this campaign. Earlier lines stay as heard. That line stays as heard.'),
    ).toBeTruthy();
  });

  it("Keep sends the keep and says the word stays as heard", async () => {
    const { api, keeps } = fakeApi([wren]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: 'Keep "Vorrun"' }));
    expect(await screen.findByText("✅ Got it: DMbot won't change \"Vorrun\" in this campaign.")).toBeTruthy();
    expect(keeps).toEqual(["h1"]);
  });

  it("each refusal says its plain words, and the list is read again", async () => {
    const cases: [string, string][] = [
      ["expired", "⌛ This question is closed, so the words stay as heard."],
      ["gone", "This question is closed: that person stopped being recorded."],
      ["only-dm", "Only the DM can answer this."],
      ["name-gone", "That name isn't in the campaign anymore, so the words stay as heard."],
      ["cant-check", "DMbot can't check the names right now. Try again in a moment."],
      ["not-saved", "Couldn't save that. Nothing was changed. Press it again."],
      ["server", "Something went wrong. Nothing was changed. Press the button again."],
    ];
    for (const [kind, line] of cases) {
      cleanup();
      const { api } = fakeApi([wren], {
        pick: async () => {
          throw new ApiError(kind as "gone");
        },
      });
      render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
      fireEvent.click(await screen.findByRole("button", { name: "Vorrin the Tall" }));
      expect(await screen.findByText(line)).toBeTruthy();
    }
  });

  it("Later sends nothing and shows the next question", async () => {
    const { api, picks, keeps } = fakeApi([wren, ash]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "⏳ Later" }));
    expect(await screen.findByText('DMbot heard a player say "Sterling". Did they mean…')).toBeTruthy();
    expect(picks).toEqual([]);
    expect(keeps).toEqual([]);
  });

  it("says all checked when nothing is waiting", async () => {
    const { api } = fakeApi([]);
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    expect(await screen.findByText("✅ All checked. DMbot will ask after a session if it isn't sure of a name.")).toBeTruthy();
  });

  it("a double press sends once", async () => {
    let release: (v: { code: string | null }) => void = () => undefined;
    const { api, picks } = fakeApi([wren], { pick: () => new Promise((r) => (release = r)) });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    const button = await screen.findByRole("button", { name: "Vorrin the Tall" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(picks).toHaveLength(1);
    release({ code: "fixed" });
    expect(await screen.findByText(/is now written Vorrin the Tall/)).toBeTruthy();
  });

  it("says so, in an alert, when the list can't be read", async () => {
    const api = mockApi("table");
    api.heardQuestions = async () => {
      throw new Error("offline");
    };
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load this.");
  });

  it("the mock is asked by name and arguments, and a pick or keep takes its question out", async () => {
    const api: AccountApi & { calls: string[] } = mockApi("table");
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Vorrin the Tall" }));
    await screen.findByText(/is now written Vorrin the Tall/);
    expect(api.calls).toContain(`heardPick:${REF}:h1:2`);
    fireEvent.click(await screen.findByRole("button", { name: 'Keep "Sterling"' }));
    await waitFor(() => expect(api.calls).toContain(`heardKeep:${REF}:h2`));
    expect(await screen.findByText("✅ All checked. DMbot will ask after a session if it isn't sure of a name.")).toBeTruthy();
  });
});

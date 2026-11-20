/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Did they mean…?" on the Questions page (#4841 B1, #4857): the DM names the word as heard, keeps it, or puts it off with ⏳ Later.
// Tested on the card itself; the Questions page only mounts it while HEARD_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import namesArea from "../src/account/api-names";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi, type ApiHelpers, errorKinds, type HeardQuestion } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import HeardQuestionsCard from "../src/account/HeardQuestions";
import { heardWords } from "../src/content/heard-words";

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

describe("a long name in a saved answer", () => {
  it("is cut the way core cuts it", () => {
    const long = "A".repeat(70);
    const cut = `${"A".repeat(59)}…`;
    expect(heardWords.fixed("Marin", long)).toContain(`is now written ${cut},`);
    expect(heardWords.lineKept("Marin", long)).toContain(`is written ${cut} in this campaign`);
  });

  it("Type it… opens the form with its title, label and hint, and Cancel closes it (#4865)", async () => {
    const { api } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    expect(screen.getByRole("form", { name: "Type the name" })).toBeTruthy();
    expect(screen.getByText("The name, as it should be written")).toBeTruthy();
    expect(screen.getByText("For example: Hrothgar")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Type the name" })).toBeNull();
  });

  it("the box takes at most 60 characters", async () => {
    const { api } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    expect(screen.getByRole("textbox").getAttribute("maxlength")).toBe("60");
  });

  it("save sends the question id and the typed name to the route", async () => {
    const { api, names } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    await waitFor(() => expect(names).toEqual([["h1", "Hrothgar"]]));
  });

  it("a saved typed name shows the same words as a picked name", async () => {
    const { api } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(
      await screen.findByText(
        '✅ Got it: "Vorrun" is now written Hrothgar, in that line and from now on in this campaign. Earlier lines stay as heard. Hrothgar is new: it waits in New names to check.',
      ),
    ).toBeTruthy();
  });

  it("a typed name saved as new shows its saved line and the new-name sentence; a plain fixed name shows only the saved line", async () => {
    const { api } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(
      await screen.findByText(
        '✅ Got it: "Vorrun" is now written Hrothgar, in that line and from now on in this campaign. Earlier lines stay as heard. Hrothgar is new: it waits in New names to check.',
      ),
    ).toBeTruthy();
    cleanup();
    const plain = fakeApi([wren]);
    render(<HeardQuestionsCard api={plain.api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Vorrin the Tall" }));
    expect(await screen.findByText('✅ Got it: "Vorrun" is now written Vorrin the Tall, in that line and from now on in this campaign. Earlier lines stay as heard.')).toBeTruthy();
    expect(screen.queryByText(/is new: it waits in New names to check/)).toBeNull();
  });

  it("each refusal of a typed name says its words, and the typed text stays in the box", async () => {
    const cases: [string, string][] = [
      ["empty", "No name was typed. Type one and press Save, or press Cancel."],
      ["bar", "That has a | sign, which a name can't have. Type just the name and press Save."],
      ["unreadable", "That has characters DMbot can't read. Type it in by hand and press Save."],
      ["too long", "That's longer than 60 characters. Type just the name and press Save."],
      ["too many words", "That's more than 8 words. Type just the name and press Save."],
      ["link", "That's a link, not a name. Type just the name and press Save."],
      ["secret", "That would put a secret name in the transcript, which everyone in the server can read, so DMbot won't write it. Pick another answer, or ignore the question."],
      ["two", "Two names in this campaign are spelled exactly like that, so DMbot can't tell which you mean. Pick a button, or give one of them a different spelling on the Names page."],
      ["a reason the page has no words for", "DMbot can't check the names right now. Try again in a moment."],
    ];
    for (const [reason, line] of cases) {
      cleanup();
      const { api } = typeApi([wren], async () => {
        const err = new ApiError("typed-problem");
        err.reason = reason;
        throw err;
      });
      render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
      fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
      fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hroth" } });
      fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
      expect(await screen.findByText(line)).toBeTruthy();
      expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Hroth");
    }
  });

  it("a name already in the campaign says so", async () => {
    const { api } = typeApi([wren], async () => {
      throw new ApiError("typed-refused");
    });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Wren" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("That name is already in this campaign (maybe as a secret one), so DMbot can't add it again. Pick a button, or look it up on the Names page.")).toBeTruthy();
  });

  it("a closed question closes its form as before", async () => {
    const { api } = typeApi([wren], async () => {
      throw new ApiError("expired");
    });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("⌛ This question is closed, so the words stay as heard.")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Type the name" })).toBeNull();
  });

  it("a blank box says the empty words on the page, without a round trip, and the name sent is trimmed (#4865)", async () => {
    const { api, names } = typeApi([wren], async () => ({ code: "fixed-new" }));
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "   " } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("No name was typed. Type one and press Save, or press Cancel.")).toBeTruthy();
    expect(names).toEqual([]);
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "  Hrothgar  " } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    await waitFor(() => expect(names).toEqual([["h1", "Hrothgar"]]));
  });

  it("only the DM can answer: that closes the form, with the words", async () => {
    const { api } = typeApi([wren], async () => {
      throw new ApiError("only-dm");
    });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("Only the DM can answer this.")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Type the name" })).toBeNull();
  });

  it("focus: opening the form focuses the box, a refusal puts it back, Cancel returns to Type it…, a saved name goes to its line", async () => {
    let attempt = 0;
    const { api } = typeApi([wren], async () => {
      attempt += 1;
      if (attempt === 1) {
        const err = new ApiError("typed-problem");
        err.reason = "link";
        throw err;
      }
      return { code: "fixed-new" };
    });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "https://x" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("That's a link, not a name. Type just the name and press Save.")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Type it…" }));
    fireEvent.click(screen.getByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    const saved = await screen.findByText(
      '✅ Got it: "Vorrun" is now written Hrothgar, in that line and from now on in this campaign. Earlier lines stay as heard. Hrothgar is new: it waits in New names to check.',
    );
    expect(document.activeElement).toBe(saved);
  });

  it("focus: a closed question sends focus to its alert line", async () => {
    const { api } = typeApi([wren], async () => {
      throw new ApiError("expired");
    });
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hrothgar" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    const closed = await screen.findByText("⌛ This question is closed, so the words stay as heard.");
    expect(document.activeElement).toBe(closed);
  });

  it("a typed name through the real error map: a link refusal, a refused name, a saved name, and a refusal with no reason", async () => {
    const through = (queued: unknown, look: { state: string; error?: string; reason?: string; code?: string } | null) => {
      const helpers = {
        call: async (method: string) => (method === "POST" ? queued : {}),
        restoreCall: async () => ({}),
        waitForAnswer: async (_path: string, onLook?: (got: unknown) => void) => {
          if (look === null) throw new Error("no answer");
          onLook?.(look);
          if (look.state === "failed") throw new ApiError(errorKinds[look.error ?? ""] ?? "server");
          return look;
        },
        root: "/api",
        url: async (path: string) => path,
      } as unknown as ApiHelpers;
      return namesArea(helpers).heardType!;
    };
    const linked = through({ answer: "a1" }, { state: "failed", error: "typed_problem", reason: "link" });
    await expect(linked(REF, "h1", "https://x")).rejects.toMatchObject({ kind: "typed-problem", reason: "link" });
    const refused = through({ answer: "a1" }, { state: "failed", error: "typed_refused" });
    await expect(refused(REF, "h1", "Wren")).rejects.toMatchObject({ kind: "typed-refused" });
    const savedAnswer = through({ answer: "a1" }, { state: "done", code: "fixed-new" });
    await expect(savedAnswer(REF, "h1", "Hrothgar")).resolves.toEqual({ code: "fixed-new" });
    const noReason = through({ answer: "a1" }, { state: "failed", error: "typed_problem" });
    const { api } = typeApi([wren], async () => ({ code: null }));
    api.heardType = noReason;
    render(<HeardQuestionsCard api={api} campaignRef={REF} id="hq" />);
    fireEvent.click(await screen.findByRole("button", { name: "Type it…" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "Hroth" } });
    fireEvent.submit(screen.getByRole("form", { name: "Type the name" }));
    expect(await screen.findByText("DMbot can't check the names right now. Try again in a moment.")).toBeTruthy();
  });
});

/** An API that holds `list` and answers a typed name by `type`; it records each name sent with its question (#4865). */
function typeApi(list: HeardQuestion[], type: (name: string) => Promise<{ code: string | null }>) {
  const names: [string, string][] = [];
  const api = mockApi("table");
  api.heardQuestions = async () => ({ total: list.length, questions: structuredClone(list) });
  api.heardType = async (_ref, question, name) => {
    names.push([question, name]);
    return type(name);
  };
  return { api, names };
}

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The cue checks on the recap page (#2084): a refused save lists its cue problems under the text
// box and stays unsaved; a clean save works as before; the cue words help shows the words the API
// sends. The API decides what is wrong, so the page is only shown what it says. The cue parts
// show only while RECAP_AUDIO_LIVE is true (false today), so these tests switch it on.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  httpApi,
  readCueProblems,
  readCueWords,
  type CueProblem,
  type CueWords,
} from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import {
  cueProblemLine,
  cueProblemsHeading,
  cueProblemsMore,
  cueWords,
  text,
} from "../src/content/recaps";

const live = vi.hoisted(() => ({ audio: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  cleanup();
  live.audio = false;
});

const ready = mockRecaps()[0]!;
const path = `/account/recaps/${ready.ref}`;

// Invented words, as the API would send them.
const words: CueWords = {
  moods: ["calm", "tense", "somber"],
  synonyms: { sad: "somber" },
  pacing: ["pause", "long pause"],
};
const problems: CueProblem[] = [
  {
    paragraph: 2,
    near: "the gate 《glumm》 opened",
    cue: "《glumm》",
    kind: "unknown",
    suggest: "gloom",
  },
  {
    paragraph: 3,
    near: "she waited ((pause",
    cue: "((pause",
    kind: "unclosed",
    suggest: null,
  },
];

function api() {
  const a = mockApi("table");
  const recap = a.recap!.bind(a);
  a.recap = async (ref) => ({ ...(await recap(ref)), cueWords: words });
  return a;
}

async function openEditor(a: ReturnType<typeof api>) {
  render(<Recaps api={a} path={path} />);
  fireEvent.click(await screen.findByRole("button", { name: text.edit }));
}

describe("a save with cue problems", () => {
  it("lists them under the text box, keeps the edit open and unsaved", async () => {
    live.audio = true;
    const a = api();
    a.saveRecap = async () => {
      throw new ApiError("bad-cues", problems);
    };
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Nothing was saved. Some cues need fixing. See the list under the text box.",
    );
    expect(screen.getByText(cueProblemsHeading(2))).toBeTruthy();
    expect(screen.getByText(cueProblemLine(problems[0]!))).toBeTruthy();
    expect(screen.getByText(cueProblemLine(problems[1]!))).toBeTruthy();
    const box = screen.getByLabelText(text.editLabel);
    expect(box.getAttribute("aria-invalid")).toBe("true");
    expect(box.getAttribute("aria-describedby")).toBe("recap-cue-problems");
    // Still editing: the text box and the save button are there, and nothing says it was saved.
    expect(screen.getByRole("button", { name: text.save })).toBeTruthy();
    expect(screen.queryByText(text.saved)).toBeNull();
    expect(screen.getByText(cueWords.paragraphs)).toBeTruthy();
  });

  it("clears the list when the next save goes through", async () => {
    live.audio = true;
    const a = api();
    const save = a.saveRecap!.bind(a);
    let first = true;
    a.saveRecap = async (...args) => {
      if (first) {
        first = false;
        throw new ApiError("bad-cues", problems);
      }
      return save(...args);
    };
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await screen.findByText(cueProblemsHeading(2));
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect(await screen.findByText(text.saved)).toBeTruthy();
    expect(screen.queryByText(cueProblemsHeading(2))).toBeNull();
  });

  it("saves a clean edit as before, with no problems shown", async () => {
    live.audio = true;
    const a = api();
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect(await screen.findByText(text.saved)).toBeTruthy();
    expect(screen.queryByText(cueProblemsHeading(2))).toBeNull();
    expect(a.calls).toContain(`saveRecap:${ready.ref}`);
  });
});

describe("the cue words help", () => {
  it("shows the moods, the other words for a mood and the pace words the API sent", async () => {
    live.audio = true;
    await openEditor(api());
    expect(screen.getByText(cueWords.help)).toBeTruthy();
    expect(screen.getByText("calm, tense, somber")).toBeTruthy();
    expect(screen.getByText("sad works as somber")).toBeTruthy();
    expect(screen.getByText("pause, long pause")).toBeTruthy();
  });

  it("is not there while narration is off, or when the API sent no words", async () => {
    await openEditor(api());
    expect(screen.queryByText(cueWords.help)).toBeNull();
    cleanup();
    live.audio = true;
    await openEditor(mockApi("table"));
    expect(screen.queryByText(cueWords.help)).toBeNull();
  });
});

describe("what the API sends", () => {
  it("reads the problems and ignores what it cannot use", () => {
    const one = {
      paragraph: 1,
      near: "x",
      cue: "《a》",
      kind: "empty",
      suggest: null,
    };
    expect(
      readCueProblems({ cue_problems: [one, { kind: "empty" }, null] }),
    ).toEqual([one]);
    expect(readCueProblems({ error: "bad_text" })).toEqual([]);
    expect(readCueProblems(null)).toEqual([]);
  });

  it("makes a refused save with cue problems a 'bad-cues' error, whatever the code says", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: "something_new", cue_problems: problems }),
          { status: 422 },
        ),
    );
    const real = httpApi(
      "https://api.example.test",
      fetcher as unknown as typeof fetch,
    );
    await expect(real.saveRecap!("a.b.1", "text", [])).rejects.toMatchObject({
      kind: "bad-cues",
      cueProblems: problems,
    });
  });
});

describe("the words for each problem", () => {
  it("say where it is and what to do, in plain words", () => {
    const kinds: string[] = [
      "unknown",
      "unclosed",
      "mismatched",
      "empty",
      "no_words",
      "inside_word",
    ];
    for (const kind of kinds) {
      const line = cueProblemLine({
        paragraph: 4,
        near: "by the well",
        cue: "《x》",
        kind,
        suggest: null,
      });
      expect(line.startsWith("Near “by the well” (paragraph 4): "), kind).toBe(
        true,
      );
      expect(line, kind).toMatch(
        /\b(Change|Pick|Add|Type|Use|Move|Put|Check)\b/,
      );
      expect(line, kind).not.toMatch(
        /syntax|tag|bracket|parse|token|regex|enum/i,
      );
    }
    expect(cueProblemLine(problems[0]!)).toContain("Change it to “gloom”");
    expect(cueProblemLine({ ...problems[0]!, suggest: null })).not.toContain(
      "Change it to",
    );
  });
});

describe("more of what the page and the API do", () => {
  it("keeps the typed text in the box after a refused save, and clears aria-invalid after a good one", async () => {
    live.audio = true;
    const a = api();
    const save = a.saveRecap!.bind(a);
    let first = true;
    a.saveRecap = async (...args) => {
      if (first) {
        first = false;
        throw new ApiError("bad-cues", problems);
      }
      return save(...args);
    };
    await openEditor(a);
    const box = screen.getByLabelText(text.editLabel) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "My edit 《glumm》." } });
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await screen.findByText(cueProblemsHeading(2));
    expect(
      (screen.getByLabelText(text.editLabel) as HTMLTextAreaElement).value,
    ).toBe("My edit 《glumm》.");
    await waitFor(() =>
      expect(document.activeElement?.id).toBe("recap-cue-problems"),
    );
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await screen.findByText(text.saved);
    expect(screen.queryByLabelText(text.editLabel)).toBeNull();
  });

  it("opens the cue words help by itself when a word is not known", async () => {
    live.audio = true;
    const a = api();
    a.saveRecap = async () => {
      throw new ApiError("bad-cues", problems);
    };
    await openEditor(a);
    expect(
      document.querySelector("details.cue-help")?.hasAttribute("open"),
    ).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await screen.findByText(cueProblemsHeading(2));
    expect(
      document.querySelector("details.cue-help")?.hasAttribute("open"),
    ).toBe(true);
  });

  it("shows the first five and says how many more, and says 'this cue' for one", async () => {
    live.audio = true;
    const many = Array.from({ length: 7 }, (_, i) => ({
      ...problems[1]!,
      paragraph: i + 1,
    }));
    const a = api();
    a.saveRecap = async () => {
      throw new ApiError("bad-cues", many);
    };
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    await screen.findByText(cueProblemsHeading(7));
    expect(document.querySelectorAll(".cue-problems li")).toHaveLength(5);
    expect(screen.getByText(cueProblemsMore(2))).toBeTruthy();
    expect(cueProblemsHeading(1)).toContain("this cue");
    expect(cueProblemsHeading(1)).toContain(text.save);
  });

  it("falls back to a plain note when 'bad-cues' comes with no details", async () => {
    live.audio = true;
    const a = api();
    a.saveRecap = async () => {
      throw new ApiError("bad-cues");
    };
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      cueWords.noteEmpty,
    );
    expect(screen.queryByText(/Nothing was saved, and your edits/)).toBeNull();
  });

  it("leaves a refusal without cue problems as it was", async () => {
    live.audio = true;
    const a = api();
    a.saveRecap = async () => {
      throw new ApiError("bad-text");
    };
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/too long/);
    expect(
      screen.getByLabelText(text.editLabel).getAttribute("aria-invalid"),
    ).toBeNull();
    expect(document.querySelector(".cue-problems")).toBeNull();
  });

  it("keeps 'bad_text' as it was over the wire, and a 'not editable' refusal as it was", async () => {
    const send = (status: number, body: unknown) =>
      httpApi(
        "https://api.example.test",
        (async () =>
          new Response(JSON.stringify(body), {
            status,
          })) as unknown as typeof fetch,
      );
    await expect(
      send(400, { error: "bad_text" }).saveRecap!("a.b.1", "t", []),
    ).rejects.toMatchObject({
      kind: "bad-text",
    });
    await expect(
      send(403, { error: "not_allowed", cue_problems: problems }).saveRecap!(
        "a.b.1",
        "t",
        [],
      ),
    ).rejects.toMatchObject({ kind: "not-allowed", cueProblems: [] });
  });

  it("reads the cue words and ignores a wrong shape", async () => {
    expect(readCueWords({ cue_words: words })).toEqual(words);
    expect(readCueWords({ cue_words: { moods: "calm" } })).toBeUndefined();
    expect(readCueWords({})).toBeUndefined();
    const fetcher = (async () =>
      new Response(JSON.stringify({ ref: "a.b.1", cue_words: words }), {
        status: 200,
      })) as unknown as typeof fetch;
    const got = await httpApi("https://api.example.test", fetcher).recap!(
      "a.b.1",
    );
    expect(got.cueWords).toEqual(words);
  });

  it("gives a kind it does not know a plain line with a next step", () => {
    const line = cueProblemLine({
      paragraph: 2,
      near: "by the well",
      cue: "《x》",
      kind: "brand_new",
      suggest: null,
    });
    expect(line).toBe(
      "Near “by the well” (paragraph 2): “《x》” is wrong. Check it against the cue words.",
    );
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The cue checks on the recap page (#2084): a refused save lists its cue problems under the text
// box and stays unsaved; a clean save works as before; the cue words help shows the words the API
// sends. The API decides what is wrong, so the page is only shown what it says. The cue parts
// show only while RECAP_AUDIO_LIVE is true (false today), so these tests switch it on.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, readCueProblems, type CueProblem, type CueWords } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { cueProblemLine, cueWords, text } from "../src/content/recaps";

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
  { paragraph: 2, near: "the gate 《glumm》 opened", cue: "《glumm》", kind: "unknown", suggest: "gloom" },
  { paragraph: 3, near: "she waited ((pause", cue: "((pause", kind: "unclosed", suggest: null },
];

function api() {
  const a = mockApi("table");
  const recap = a.recap.bind(a);
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
      "Some cues in the text need fixing. They are listed under the text box. Nothing was saved.",
    );
    expect(screen.getByText(cueWords.problemsHeading)).toBeTruthy();
    expect(screen.getByText(cueProblemLine(problems[0]!))).toBeTruthy();
    expect(screen.getByText(cueProblemLine(problems[1]!))).toBeTruthy();
    const box = screen.getByLabelText(text.editLabel);
    expect(box.getAttribute("aria-invalid")).toBe("true");
    expect(box.getAttribute("aria-describedby")).toBe("recap-cue-problems");
    // Still editing: the text box and the save button are there, and nothing says it was saved.
    expect(screen.getByRole("button", { name: text.save })).toBeTruthy();
    expect(screen.queryByText(text.saved)).toBeNull();
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
    await screen.findByText(cueWords.problemsHeading);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect(await screen.findByText(text.saved)).toBeTruthy();
    expect(screen.queryByText(cueWords.problemsHeading)).toBeNull();
  });

  it("saves a clean edit as before, with no problems shown", async () => {
    live.audio = true;
    const a = api();
    await openEditor(a);
    fireEvent.click(screen.getByRole("button", { name: text.save }));
    expect(await screen.findByText(text.saved)).toBeTruthy();
    expect(screen.queryByText(cueWords.problemsHeading)).toBeNull();
    expect(a.calls).toContain(`saveRecap:${ready.ref}`);
  });
});

describe("the cue words help", () => {
  it("shows the moods, the other words for a mood and the pace words the API sent", async () => {
    live.audio = true;
    await openEditor(api());
    expect(screen.getByText(cueWords.help)).toBeTruthy();
    expect(screen.getByText("calm, tense, somber")).toBeTruthy();
    expect(screen.getByText("sad (somber)")).toBeTruthy();
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
  it("reads either spelling of the field and ignores what it cannot use", () => {
    const one = { paragraph: 1, near: "x", cue: "《a》", kind: "empty", suggest: null };
    expect(readCueProblems({ cueProblems: [one] })).toEqual([one]);
    expect(readCueProblems({ cue_problems: [one, { kind: "empty" }, null] })).toEqual([one]);
    expect(readCueProblems({ error: "bad_text" })).toEqual([]);
    expect(readCueProblems(null)).toEqual([]);
  });

  it("makes a refused save with cue problems a 'bad-cues' error, whatever the code says", async () => {
    const { httpApi } = await import("../src/account/api");
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ error: "something_new", cueProblems: problems }), { status: 422 }),
    );
    const real = httpApi("https://api.example.test", fetcher as unknown as typeof fetch);
    await expect(real.saveRecap!("a.b.1", "text", [])).rejects.toMatchObject({
      kind: "bad-cues",
      cueProblems: problems,
    });
  });
});

describe("the words for each problem", () => {
  it("say where it is and what to do, in plain words", () => {
    const kinds: CueProblem["kind"][] = ["unknown", "unclosed", "mismatched", "empty", "no_words", "inside_word"];
    for (const kind of kinds) {
      const line = cueProblemLine({ paragraph: 4, near: "by the well", cue: "《x》", kind, suggest: null });
      expect(line.startsWith("Paragraph 4: "), kind).toBe(true);
      expect(line, kind).not.toMatch(/syntax|tag|bracket|parse|token|regex|enum/i);
    }
    expect(cueProblemLine(problems[0]!)).toContain("Did you mean “gloom”?");
    expect(cueProblemLine({ ...problems[0]!, suggest: null })).not.toContain("Did you mean");
  });
});

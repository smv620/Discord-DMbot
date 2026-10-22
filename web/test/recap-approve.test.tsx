/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Approve this recap" on the recap page (#2084, #2083, #2419): the three ways to approve, the
// audio button greyed with its reason, what is shown once approved (Copy, Post later, Keep it), the
// cue problems an approval can be refused with, and the refusals. The audio button is greyed
// ("not set up") while RECAP_AUDIO_LIVE is false, so these tests switch it on where it should work.
// The API decides; the page shows what it says.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type CueProblem } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import {
  approveErrors,
  approveRefreshed,
  approveWords as w,
} from "../src/content/recap-approve";
import { cueProblemsHeading, text } from "../src/content/recaps";

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

async function open(api = mockApi("table")) {
  render(<Recaps api={api} path={path} />);
  await screen.findByText(/The party crossed the Brynwater/);
  return api as ReturnType<typeof mockApi>;
}

describe("the three ways to approve", () => {
  it("shows all three, with the audio one greyed while narration is off", async () => {
    await open();
    expect(screen.getByRole("heading", { name: w.heading })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.postText })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.keepIt })).toBeTruthy();
    const audio = (await screen.findByRole("button", { name: w.makeAudio })) as HTMLButtonElement;
    await waitFor(() => expect(audio.disabled).toBe(true));
    expect(await screen.findByText(w.audioWhy.not_set_up)).toBeTruthy();
  });

  it("✅ approves with the text-only choice and posts, in one call", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.postText }));
    expect(await screen.findByText(text.posting)).toBeTruthy();
    expect(api.calls).toContain(`approveRecap:${ready.ref}:text:post`);
    expect(api.calls.some((c) => c.startsWith("postRecap"))).toBe(false); // no second call needed
    expect(screen.queryByRole("button", { name: w.postText })).toBeNull();
  });

  it("✅ still posts when an older API only approves (it then answers with a draft)", async () => {
    const api = mockApi("table");
    const approve = api.approveRecap!.bind(api);
    api.approveRecap = async (ref, choice) => approve(ref, choice); // ignores `post`
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.postText }));
    expect(await screen.findByText(text.posting)).toBeTruthy();
    expect(api.calls).toContain(`approveRecap:${ready.ref}:text`);
    expect(api.calls).toContain(`postRecap:${ready.ref}`);
  });

  it("✅ says it was approved but not posted when the post that follows fails", async () => {
    const api = mockApi("table");
    const approve = api.approveRecap!.bind(api);
    api.approveRecap = async (ref, choice) => approve(ref, choice); // an older API: approve only
    api.decideRecap = async () => {
      throw new ApiError("network");
    };
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.postText }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      `${w.approvedNotPostedPrefix} ${approveErrors.network}`,
    );
    expect(screen.getByText(w.notPosted)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.postLater })).toBeTruthy();
  });

  it("📋 approves and posts nothing, and says so with the text to copy", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    expect(await screen.findByText(w.notPosted)).toBeTruthy();
    expect(api.calls).toContain(`approveRecap:${ready.ref}:text`);
    expect(api.calls.some((c) => c.startsWith("postRecap"))).toBe(false);
    expect(screen.getByRole("button", { name: w.copy })).toBeTruthy();
    expect(screen.queryByText(text.dropped)).toBeNull(); // approved and kept is not "set aside"
    await waitFor(() => expect(document.activeElement?.textContent).toBe(w.notPosted));
  });

  it("copies the clean text, without cues", async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (t: string) => void written.push(t) },
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    fireEvent.click(await screen.findByRole("button", { name: w.copy }));
    expect(await screen.findByText(w.copied)).toBeTruthy();
    expect(written).toEqual([ready.text]);
    expect(written[0]).not.toMatch(/\[music|《/);
  });

  it("says plainly when the browser won't copy", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async () => {
          throw new Error("no");
        },
      },
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    fireEvent.click(await screen.findByRole("button", { name: w.copy }));
    expect(await screen.findByText(w.copyFailed)).toBeTruthy();
  });

  it("an approved, not posted recap can still be posted later", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    fireEvent.click(await screen.findByRole("button", { name: w.postLater }));
    expect(await screen.findByText(text.posting)).toBeTruthy();
    expect(api.calls).toContain(`postRecap:${ready.ref}`);
  });

  it("says what was approved when the page is opened later, and offers Post then", async () => {
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await recap(ref)),
      approval: { approved: true, choice: "text", approvedAt: 1791000100, canApprove: false },
    });
    await open(api);
    expect(screen.getByText(w.notPosted)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.postLater })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.postText })).toBeNull();
  });

  it("shows nothing when the recap is not ready to approve", async () => {
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await recap(ref)),
      approval: { approved: false, choice: "", approvedAt: null, canApprove: false },
    });
    await open(api);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });
});

describe("🎙️ approve and make audio to review", () => {
  async function withReason(why: "plan" | "allowance" | "not_set_up" | "unavailable" | "") {
    live.audio = true;
    const api = mockApi("table");
    const choices = api.recapChoices!.bind(api);
    api.recapChoices = async (ref) => ({ ...(await choices(ref)), audioUnavailable: why });
    await open(api);
    return api;
  }

  it("is greyed with its reason for each way it can't be made", async () => {
    for (const why of ["plan", "allowance", "not_set_up", "unavailable"] as const) {
      cleanup();
      await withReason(why);
      const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
      await waitFor(() => expect(button.disabled).toBe(true));
      expect(await screen.findByText(w.audioWhy[why])).toBeTruthy();
      expect(button.getAttribute("aria-describedby")).toBe("recap-audio-why");
    }
  });

  it("is greyed, not hidden, when the plan couldn't be checked", async () => {
    live.audio = true;
    const api = mockApi("table");
    api.recapChoices = async () => {
      throw new ApiError("network");
    };
    await open(api);
    expect(await screen.findByText(w.audioUnknown)).toBeTruthy();
    expect((screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reads an older API (no reason, plan has audio) as available", async () => {
    live.audio = true;
    const api = mockApi("table");
    const choices = api.recapChoices!.bind(api);
    api.recapChoices = async (ref) => {
      const { audioUnavailable: _gone, ...old } = await choices(ref);
      return old;
    };
    await open(api);
    const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
  });

  it("asks which, then sends the choice and says what is being made", async () => {
    const api = await withReason("");
    const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    for (const label of [w.choices.voice, w.choices.music, w.choices.both]) {
      expect(await screen.findByRole("button", { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: w.choices.text })).toBeNull(); // that is the other button
    fireEvent.click(screen.getByRole("button", { name: w.choices.both }));
    expect(await screen.findByText(w.done.both)).toBeTruthy();
    expect(api.calls).toContain(`approveRecap:${ready.ref}:both`);
    expect(screen.getByText(w.editEnds)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.postLater })).toBeNull(); // nothing to post yet
    await waitFor(() => expect(document.activeElement?.textContent).toBe(w.done.both));
  });

  it("sends each of the three choices as it is", async () => {
    for (const choice of ["voice", "music", "both"] as const) {
      cleanup();
      const api = await withReason("");
      const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
      await waitFor(() => expect(button.disabled).toBe(false));
      fireEvent.click(button);
      fireEvent.click(await screen.findByRole("button", { name: w.choices[choice] }));
      await screen.findByText(w.done[choice]);
      expect(api.calls).toContain(`approveRecap:${ready.ref}:${choice}`);
    }
  });

  it("can be backed out of without asking the API", async () => {
    const api = await withReason("");
    const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: w.back }));
    expect(screen.getByRole("button", { name: w.makeAudio })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("approveRecap"))).toBe(false);
  });

  it("once the audio is made, offers Post to the table and Keep it, don't post", async () => {
    live.audio = true;
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => {
      const r = await recap(ref);
      return {
        ...r,
        approval: { approved: true, choice: "both", approvedAt: 1791000100, canApprove: false },
        audio: { state: "ready", error: "", current: true, canAskAgain: true },
      };
    };
    await open(api);
    expect(screen.getByText(w.review)).toBeTruthy();
    expect(screen.queryByText(w.notPosted)).toBeNull(); // not decided yet
    fireEvent.click(screen.getByRole("button", { name: w.keepAudio }));
    expect(await screen.findByText(w.notPosted)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.copy })).toBeTruthy();
    expect(screen.getByRole("button", { name: w.postLater })).toBeTruthy(); // still postable
    expect(api.calls.some((c) => c.startsWith("postRecap"))).toBe(false);
  });

  it("while the audio is being made, says so and offers nothing to post", async () => {
    live.audio = true;
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await recap(ref)),
      approval: { approved: true, choice: "voice", approvedAt: 1791000100, canApprove: false },
      audio: { state: "queued", error: "", current: false, canAskAgain: false },
    });
    await open(api);
    expect(screen.getByText(w.was.voice)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.postLater })).toBeNull();
  });
});

describe("a refused approval", () => {
  async function pickWith(error: ApiError, label = w.postText) {
    const api = mockApi("table");
    api.approveRecap = async () => {
      throw error;
    };
    await open(api);
    const button = screen.getByRole("button", { name: label });
    button.focus();
    (document.activeElement as HTMLElement | null)?.blur(); // a browser drops focus when the button disables
    fireEvent.click(button);
    return api;
  }

  it("lists the cue problems, keeps the buttons and says to edit first", async () => {
    const problems: CueProblem[] = [
      { paragraph: 2, near: "the gate opened", cue: "《glumm》", kind: "unknown", suggest: "gloom" },
    ];
    await pickWith(new ApiError("bad-cues", problems));
    expect(screen.queryByRole("alert")).toBeNull(); // the list heading carries it, once
    expect(await screen.findByText(cueProblemsHeading(1, "approve"))).toBeTruthy();
    expect(cueProblemsHeading(1, "approve")).toContain("Nothing was approved");
    expect(screen.getByText(/“《glumm》” is not a cue word/)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.postText })).toBeTruthy(); // can try again
    await waitFor(() =>
      expect(document.activeElement?.outerHTML.slice(0, 80)).toContain("recap-cue-problems"),
    );
  });

  it("says plainly when the plan has no audio, and greys the button", async () => {
    live.audio = true;
    const api = mockApi("table");
    api.approveRecap = async () => {
      throw new ApiError("not-in-plan");
    };
    await open(api);
    const button = screen.getByRole("button", { name: w.makeAudio }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: w.choices.voice }));
    expect((await screen.findByRole("alert")).textContent).toBe(approveErrors["not-in-plan"]);
  });

  it("says plainly when the narration can't be made right now", async () => {
    await pickWith(new ApiError("audio-unavailable"));
    expect((await screen.findByRole("alert")).textContent).toBe(approveErrors["audio-unavailable"]);
  });

  it("reads the page again when the recap was already decided", async () => {
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    let reads = 0;
    api.recap = async (ref) => {
      reads += 1;
      const r = await recap(ref);
      return reads > 1
        ? { ...r, approval: { approved: true, choice: "text", approvedAt: 1791000100, canApprove: false } }
        : r;
    };
    api.approveRecap = async () => {
      throw new ApiError("not-allowed");
    };
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.keepIt }));
    expect(await screen.findByText(w.notPosted)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe(approveRefreshed);
  });

  it("says where to look when the API refuses over cues without a list", async () => {
    const fetcher = (async () =>
      new Response(JSON.stringify({ error: "cue_problems", cue_problems: [] }), {
        status: 422,
      })) as unknown as typeof fetch;
    const real = httpApi("https://api.example.test", fetcher);
    await expect(real.approveRecap!("a.b.1", "voice")).rejects.toMatchObject({
      kind: "bad-cues",
    });
    await pickWith(new ApiError("bad-cues"));
    expect((await screen.findByRole("alert")).textContent).toBe(w.cuesUnlisted);
  });

  it("sends the choice to the API's own address, with post only when asked, and reads its refusals", async () => {
    const seen: { url: string; body: string }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      seen.push({ url, body: String(init.body) });
      return new Response(
        JSON.stringify({
          error: "cue_problems",
          cue_problems: [
            { paragraph: 1, near: "x", cue: "《a》", kind: "empty", suggest: null, words: "x" },
          ],
        }),
        { status: 422 },
      );
    }) as unknown as typeof fetch;
    const real = httpApi("https://api.example.test", fetcher);
    await expect(real.approveRecap!("a.b.1", "both")).rejects.toMatchObject({ kind: "bad-cues" });
    await expect(real.approveRecap!("a.b.1", "text", { post: true })).rejects.toMatchObject({
      kind: "bad-cues",
    });
    expect(seen).toEqual([
      { url: "https://api.example.test/me/recaps/a.b.1/approve", body: JSON.stringify({ choice: "both" }) },
      {
        url: "https://api.example.test/me/recaps/a.b.1/approve",
        body: JSON.stringify({ choice: "text", post: true }),
      },
    ]);
  });

  it("reads the API's audio_unavailable refusal", async () => {
    const fetcher = (async () =>
      new Response(JSON.stringify({ error: "audio_unavailable" }), {
        status: 409,
      })) as unknown as typeof fetch;
    const real = httpApi("https://api.example.test", fetcher);
    await expect(real.approveRecap!("a.b.1", "voice")).rejects.toMatchObject({
      kind: "audio-unavailable",
    });
  });
});

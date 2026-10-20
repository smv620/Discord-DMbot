/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Approve the text" on the recap page (#2084, #2083): approve, then the four choices, showing only
// what the plan includes; the cue problems an approval can be refused with; the refusals. It shows
// only while RECAP_AUDIO_LIVE is true (false today), so these tests switch it on. The API decides;
// the page shows what it says.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type CueProblem } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { approveErrors, approveWords as w } from "../src/content/recap-approve";
import { cueProblemsHeading } from "../src/content/recaps";

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

describe("approve the text", () => {
  it("is not on the page while narration is off", async () => {
    await open();
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("asks first, then offers the four choices a plan with audio has", async () => {
    live.audio = true;
    await open();
    expect(screen.getByRole("heading", { name: w.heading })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.choices.voice })).toBeNull(); // not before approving
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    for (const label of Object.values(w.choices)) {
      expect(await screen.findByRole("button", { name: label })).toBeTruthy();
    }
    expect(screen.queryByText(w.textOnlyNote)).toBeNull();
  });

  it("offers only Text only where the plan has no audio", async () => {
    live.audio = true;
    const api = mockApi("table");
    const choices = api.recapChoices!.bind(api);
    api.recapChoices = async (ref) => ({ ...(await choices(ref)), canAudio: false });
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    expect(await screen.findByRole("button", { name: w.choices.text })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.choices.voice })).toBeNull();
    expect(screen.queryByRole("button", { name: w.choices.both })).toBeNull();
    expect(screen.getByText(w.textOnlyNote)).toBeTruthy();
  });

  it("sends the choice, then says what is being made and that an edit asks again", async () => {
    live.audio = true;
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    fireEvent.click(await screen.findByRole("button", { name: w.choices.both }));
    expect(await screen.findByText(w.done.both)).toBeTruthy();
    expect(screen.getByText(w.editEnds)).toBeTruthy();
    expect(api.calls).toContain(`approveRecap:${ready.ref}:both`);
    expect(screen.queryByRole("button", { name: w.approve })).toBeNull();
    await waitFor(() => expect((document.activeElement as HTMLElement | null)?.getAttribute("role")).toBe("status"));
  });

  it("sends each of the four choices as it is", async () => {
    live.audio = true;
    for (const [choice, label] of Object.entries(w.choices)) {
      cleanup();
      const api = await open();
      fireEvent.click(screen.getByRole("button", { name: w.approve }));
      fireEvent.click(await screen.findByRole("button", { name: label }));
      await screen.findByText(w.done[choice as keyof typeof w.done]);
      expect(api.calls).toContain(`approveRecap:${ready.ref}:${choice}`);
    }
  });

  it("can be backed out of without asking the API", async () => {
    live.audio = true;
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    fireEvent.click(await screen.findByRole("button", { name: w.back }));
    expect(screen.getByRole("button", { name: w.approve })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("approveRecap"))).toBe(false);
  });

  it("says what was approved when the page is opened later", async () => {
    live.audio = true;
    const api = mockApi("table");
    const recap = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await recap(ref)),
      approval: { approved: true, choice: "music", approvedAt: 1791000100, canApprove: false },
    });
    await open(api);
    expect(screen.getByText(w.was.music)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.approve })).toBeNull();
  });

  it("shows nothing when the recap is not ready to approve", async () => {
    live.audio = true;
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

describe("a refused approval", () => {
  async function pickWith(error: ApiError) {
    live.audio = true;
    const api = mockApi("table");
    api.approveRecap = async () => {
      throw error;
    };
    await open(api);
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    fireEvent.click(await screen.findByRole("button", { name: w.choices.voice }));
  }

  it("lists the cue problems, keeps the choices open and says to edit first", async () => {
    const problems: CueProblem[] = [
      { paragraph: 2, near: "the gate opened", cue: "《glumm》", kind: "unknown", suggest: "gloom" },
    ];
    await pickWith(new ApiError("bad-cues", problems));
    expect((await screen.findByRole("alert")).textContent).toBe(w.cuesListed);
    expect(screen.getByText(cueProblemsHeading(1, "approve"))).toBeTruthy();
    expect(cueProblemsHeading(1, "approve")).toContain("Nothing was approved");
    expect(screen.getByText(/“《glumm》” is not a cue word/)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.choices.voice })).toBeTruthy(); // can try again
    await waitFor(() => expect(document.activeElement?.id).toBe("recap-cue-problems"));
  });

  it("says plainly when the plan has no audio", async () => {
    await pickWith(new ApiError("not-in-plan"));
    expect((await screen.findByRole("alert")).textContent).toBe(approveErrors["not-in-plan"]);
  });

  it("reads the page again when the recap was already decided", async () => {
    live.audio = true;
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
    fireEvent.click(screen.getByRole("button", { name: w.approve }));
    fireEvent.click(await screen.findByRole("button", { name: w.choices.voice }));
    expect(await screen.findByText(w.was.text)).toBeTruthy();
  });

  it("sends the choice to the API's own address, and reads its cue problems over the wire", async () => {
    const seen: { url: string; body: string }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      seen.push({ url, body: String(init.body) });
      return new Response(
        JSON.stringify({
          error: "cue_problems",
          cue_problems: [{ paragraph: 1, near: "x", cue: "《a》", kind: "empty", suggest: null, words: "x" }],
        }),
        { status: 422 },
      );
    }) as unknown as typeof fetch;
    const real = httpApi("https://api.example.test", fetcher);
    await expect(real.approveRecap!("a.b.1", "both")).rejects.toMatchObject({ kind: "bad-cues" });
    expect(seen[0]).toEqual({
      url: "https://api.example.test/me/recaps/a.b.1/approve",
      body: JSON.stringify({ choice: "both" }),
    });
  });
});

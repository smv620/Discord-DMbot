/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Import sessions page (#2107): files, who is who, what would be kept, dates and order, the
// refusals, and the words over the wire. It shows only while IMPORT_LIVE is true (false today), so
// these tests switch it on. The API decides who may import and who has agreed; the page shows it.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import, { campaignFromPath } from "../src/account/Import";
import { IMPORT_MAX_FILES, importPage as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const path = "/account/import/100.cmp1";
const file = (name: string, text: string, size?: number): File => {
  const f = new File([text], name, { type: "text/plain" });
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
};
const s1 = file(
  "session-1-2026-09-14.txt",
  "Cerric: hello\nAsh: hi\nMira: ok\nCerric: go",
);
const s2 = file("session-2.txt", "Cerric: again");

async function pick(files: File[]): Promise<HTMLInputElement> {
  const input = screen.getByLabelText(w.pickLabel, {
    selector: "input",
  }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
  return input;
}

function open(api = mockApi("table")) {
  live.on = true;
  render(<Import api={api} path={path} />);
  return api;
}

describe("the address", () => {
  it("reads the campaign out of the path", () => {
    expect(campaignFromPath("/account/import/100.cmp1")).toBe("100.cmp1");
    expect(campaignFromPath("/account/import/")).toBeNull();
    expect(campaignFromPath("/account/transcripts/100.cmp1")).toBeNull();
  });
});

describe("the page", () => {
  it("shows nothing while importing is not live", () => {
    render(<Import api={mockApi("table")} path={path} />);
    expect(screen.queryByText(w.pickLabel)).toBeNull();
  });

  it("previews the files and answers who is who, with the API's own suggestion", async () => {
    const api = open();
    await pick([s1, s2]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("heading", { name: w.matchHeading });
    expect(api.calls).toContain(
      "importPreview:100.cmp1:session-1-2026-09-14.txt,session-2.txt",
    );
    // Cerric in both files is one line to answer, with both files' lines counted.
    const cerric = screen.getByLabelText(
      `Cerric: ${w.matchHeading}`,
    ) as HTMLSelectElement;
    expect(screen.getAllByLabelText(/^Cerric:/)).toHaveLength(1);
    expect(cerric.selectedOptions[0]?.textContent).toBe("Stormy");
    expect(
      screen.getByText(new RegExp(w.suggestedBecause("remembered"))),
    ).toBeTruthy();
    // Nobody else is guessed: they start as Leave out.
    expect(
      (screen.getByLabelText(`Ash: ${w.matchHeading}`) as HTMLSelectElement)
        .selectedOptions[0]?.textContent,
    ).toBe(w.leaveOut);
    // Someone who has not agreed is marked in the list.
    expect(within(cerric).getByText(`Ash (${w.notAgreedMark})`)).toBeTruthy();
  });

  it("shows a speaker who has not agreed as dropped, with how a player agrees", async () => {
    const api = open();
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("heading", { name: w.matchHeading });
    expect(
      screen.getByText(
        /A player agrees in Discord, the same as for a live session/,
      ),
    ).toBeTruthy();
    const ash = await screen.findByLabelText(`Ash: ${w.matchHeading}`);
    fireEvent.change(ash, { target: { value: "2" } });
    expect(
      await screen.findByText(w.droppedReason.not_agreed ?? ""),
    ).toBeTruthy(); // marked as soon as it is picked
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    expect(
      await screen.findByText(
        /Ash: 0 kept; 1 dropped: hasn't agreed to be recorded: their lines are left out/,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Cerric: 2 kept/)).toBeTruthy();
    expect(api.calls).toContain("importCheck:100.cmp1");
  });

  it("asks for a date it could not guess, orders the sessions, and imports them in that order", async () => {
    const api = open();
    await pick([s1, s2]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("heading", { name: w.datesHeading });
    const start = screen.getByRole("button", {
      name: w.importButton,
    }) as HTMLButtonElement;
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    expect(start.disabled).toBe(true); // session-2 has no date yet
    expect(screen.getAllByText(w.dateGuess)).toHaveLength(1); // only the guessed one says so
    fireEvent.input(screen.getByLabelText(w.dateLabel("session-2.txt")), {
      target: { value: "2026-09-21" },
    });
    expect(start.disabled).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: w.moveUp("session-2.txt") }),
    );
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await waitFor(() =>
      expect(api.calls).toContain(
        "importStart:100.cmp1:1=session-2.txt@2026-09-21,2=session-1-2026-09-14.txt@2026-09-14",
      ),
    );
    expect(await screen.findByText(w.progressHeading)).toBeTruthy();
  });

  it("follows the work until it is done, and says when one failed", async () => {
    const api = mockApi("table");
    api.importProgress = async () => ({
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "done", reason: null },
        {
          file: "b.txt",
          date: "2026-09-21",
          state: "failed",
          reason: "names_failed",
        },
      ],
    });
    api.importStart = async () => ({
      importId: "x",
      sessions: [],
      estimate: { sessions: 2, fastCalls: 1 },
    });
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    expect(await screen.findByText(w.someFailed)).toBeTruthy();
    expect(
      screen.getByText(new RegExp(w.failedReason.names_failed ?? "")),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: w.seeTranscripts }).getAttribute("href"),
    ).toBe("/account/transcripts/100.cmp1");
  });
});

describe("the limits", () => {
  it("stops more than the most files, before anything is sent", async () => {
    const api = open();
    await pick(
      Array.from({ length: IMPORT_MAX_FILES + 1 }, (_, i) =>
        file(`s${i}.txt`, "A: x"),
      ),
    );
    expect(screen.getByRole("alert").textContent).toBe(w.tooManyFiles);
    expect(
      (screen.getByRole("button", { name: w.preview }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(api.calls.some((c) => c.startsWith("importPreview"))).toBe(false);
  });

  it("names a file that is too big or not a .txt", async () => {
    open();
    await pick([file("big.txt", "A: x", 3 * 1024 * 1024)]);
    expect(screen.getByRole("alert").textContent).toBe(w.fileTooBig("big.txt"));
    await pick([file("notes.docx", "A: x")]);
    expect(screen.getByRole("alert").textContent).toBe(
      w.fileNotTxt("notes.docx"),
    );
  });

  it("says what is wrong with a file the API could not use, and stops when none can be", async () => {
    open();
    await pick([file("empty.txt", "no speakers here")]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(await screen.findByText(w.fileError.empty ?? "")).toBeTruthy();
    expect(screen.getByText(w.noUsableFiles)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: w.matchHeading })).toBeNull();
  });
});

describe("a refusal", () => {
  async function refused(error: ApiError) {
    const api = mockApi("table");
    api.importPreview = async () => {
      throw error;
    };
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
  }

  it("says plainly that only a game master can import", async () => {
    await refused(new ApiError("not-allowed"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["not-allowed"],
    );
  });

  it("says importing is part of the paid plans, and not part of Try It", async () => {
    await refused(new ApiError("no-paid-plan"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["no-paid-plan"],
    );
    cleanup();
    await refused(new ApiError("try-it-campaign"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["try-it-campaign"],
    );
  });

  it("offers to sign in again when the session ended", async () => {
    await refused(new ApiError("signed-out"));
    expect(await screen.findByRole("link", { name: w.signIn })).toBeTruthy();
  });

  it("says what to do when every line in a file would be dropped", async () => {
    const api = mockApi("table");
    api.importStart = async () => {
      throw new ApiError("no-lines-kept");
    };
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["no-lines-kept"],
    );
    expect(screen.getByRole("button", { name: w.importButton })).toBeTruthy(); // can try again
  });
});

describe("over the wire", () => {
  const sent: { url: string; body: string }[] = [];
  const answer = (status: number, body: unknown) =>
    (async (url: string, init: RequestInit) => {
      sent.push({ url, body: String(init.body ?? "") });
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch;

  it("sends the files as text to the campaign's own address", async () => {
    sent.length = 0;
    const real = httpApi(
      "https://api.example.test",
      answer(200, { files: [], estimate: { sessions: 0, fastCalls: 0 } }),
    );
    await real.importPreview!("100.cmp1", [{ name: "a.txt", text: "A: x" }]);
    expect(sent[0]).toEqual({
      url: "https://api.example.test/me/campaigns/100.cmp1/imports/preview",
      body: JSON.stringify({ files: [{ name: "a.txt", text: "A: x" }] }),
    });
  });

  it("searches the people with the typed words, safely", async () => {
    sent.length = 0;
    const real = httpApi(
      "https://api.example.test",
      answer(200, { members: [] }),
    );
    await real.importMembers!("100.cmp1", "st&r");
    expect(sent[0]?.url).toBe(
      "https://api.example.test/me/campaigns/100.cmp1/imports/members?q=st%26r",
    );
  });

  it("reads the import routes' refusals, which come as detail", async () => {
    const real = (status: number, detail: string) =>
      httpApi("https://api.example.test", answer(status, { detail }))
        .importPreview!("100.cmp1", []);
    await expect(real(403, "not_a_dm")).rejects.toMatchObject({
      kind: "not-allowed",
    });
    await expect(real(402, "not_on_a_paid_plan")).rejects.toMatchObject({
      kind: "no-paid-plan",
    });
    await expect(real(402, "try_it_campaign")).rejects.toMatchObject({
      kind: "try-it-campaign",
    });
    await expect(real(413, "too_many_files")).rejects.toMatchObject({
      kind: "too-many-files",
    });
    await expect(real(422, "no_lines_kept")).rejects.toMatchObject({
      kind: "no-lines-kept",
    });
    await expect(real(422, "bad_mapping")).rejects.toMatchObject({
      kind: "bad-mapping",
    });
  });
});

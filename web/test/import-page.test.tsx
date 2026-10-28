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

describe("Google Doc links", () => {
  const link = (text: string) =>
    fireEvent.input(screen.getByLabelText(w.linkLabel), {
      target: { value: text },
    });
  const add = (text: string) => {
    link(text);
    fireEvent.click(screen.getByRole("button", { name: w.addLink }));
  };
  const docUrl = (id: string) =>
    `https://docs.google.com/document/d/${id}/edit`;

  it("adds a link to the same list as the files, and lets it be taken out", async () => {
    open();
    await pick([s1]);
    add(docUrl("abc"));
    expect(screen.getByText(w.linkName(1))).toBeTruthy();
    expect(screen.getByText(w.chosenItems(1, 1))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.removeLink(1) }));
    expect(screen.queryByText(w.linkName(1))).toBeNull();
  });

  it("says how to share a doc, before anything is sent", () => {
    open();
    expect(screen.getByText(/Scroll down to General access/)).toBeTruthy();
    expect(screen.getByText(/Anyone with the link can open it/)).toBeTruthy();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(4);
  });

  it("previews with only a link, and sends the links with the files", async () => {
    const api = open();
    add(docUrl("abc"));
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    await screen.findByRole("heading", { name: w.matchHeading });
    expect(api.calls).toContain("importPreview:100.cmp1:|links:1");
  });

  it("counts files and links together against the limit", async () => {
    const api = open();
    await pick(
      Array.from({ length: IMPORT_MAX_FILES }, (_, i) =>
        file(`s${i}.txt`, "A: x"),
      ),
    );
    add(docUrl("one-too-many"));
    // The 21st is refused, so the list stays at the most.
    expect(screen.getByRole("alert").textContent).toBe(w.tooManyToAdd);
    expect(screen.queryByText(w.linkName(1))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("importPreview"))).toBe(false);
  });

  it("says in words when a doc is not shared, never the raw code", async () => {
    open();
    add(docUrl("private-one"));
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(
      await screen.findByText(w.fileError.doc_not_shared ?? ""),
    ).toBeTruthy();
    expect(screen.queryByText("doc_not_shared")).toBeNull();
    expect(w.fileError.doc_not_shared).toContain("Anyone with the link");
    // Shown as "Google Doc 1", without the ".txt" the API puts on its name.
    expect(screen.queryByText("Google Doc 1.txt")).toBeNull();
  });

  it.each([
    ["not_a_google_doc", docUrl("notadoc")],
    ["doc_not_shared", docUrl("private")],
    ["doc_unreachable", docUrl("unreachable")],
    ["doc_too_big", docUrl("toobig")],
    ["repeated_link", docUrl("repeat")],
  ])("shows %s in words, never raw", async (code, url) => {
    open();
    add(url);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(await screen.findByText(w.fileError[code] ?? "")).toBeTruthy();
    expect(screen.queryByText(code)).toBeNull();
    expect(w.fileError[code]).toBeTruthy();
  });

  it("accepts a link with no scheme or with https", () => {
    open();
    add("docs.google.com/document/d/abc/edit");
    add("https://docs.google.com/document/d/def/edit");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(w.linkName(2))).toBeTruthy();
  });

  it("refuses an http link with its own words, and adds no row", () => {
    open();
    add("http://docs.google.com/document/d/def/edit");
    expect(screen.getByRole("alert").textContent).toBe(w.googleDocNeedsHttps);
    expect(screen.queryByText(w.linkName(1))).toBeNull();
  });

  it("refuses an upper-case HTTP link the same way", () => {
    open();
    add("HTTP://docs.google.com/document/d/def/edit");
    expect(screen.getByRole("alert").textContent).toBe(w.googleDocNeedsHttps);
    expect(screen.queryByText(w.linkName(1))).toBeNull();
  });

  it("refuses a padded http link too, since the link is trimmed first", () => {
    open();
    add("  http://docs.google.com/document/d/def/edit  ");
    expect(screen.getByRole("alert").textContent).toBe(w.googleDocNeedsHttps);
    expect(screen.queryByText(w.linkName(1))).toBeNull();
  });

  it("shows a doc that stops opening at Check, and says so at Import", async () => {
    const api = open();
    add(docUrl("later"));
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.input(await screen.findByLabelText(w.dateLabel("Google Doc 1")), {
      target: { value: "2026-09-21" },
    });
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    expect(await screen.findByText(w.fileError.doc_not_shared ?? "")).toBeTruthy();
    expect(screen.queryByText("doc_not_shared")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    expect(await screen.findByText(w.problem["bad-files-docs"] ?? "")).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("importStart"))).toBe(true);
  });

  it("says so when a link gets no answer at Preview", async () => {
    const api = open();
    api.importPreview = async () => ({ files: [], estimate: { sessions: 0, fastCalls: 0 } });
    add(docUrl("abc"));
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(await screen.findByText(w.docNoAnswer)).toBeTruthy();
  });

  it("refuses a link that is not a Google Doc, before anything is sent", () => {
    const api = open();
    add("https://example.com/notadoc");
    expect(screen.getByRole("alert").textContent).toBe(w.notAGoogleDocLink);
    expect(screen.queryByText(w.linkName(1))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("importPreview"))).toBe(false);
  });

  it("does not add the same doc twice", () => {
    open();
    add(docUrl("same"));
    add(docUrl("same"));
    expect(screen.getByRole("alert").textContent).toBe(w.linkAlreadyAdded);
    expect(screen.queryByText(w.linkName(2))).toBeNull();
  });

  it("treats the same doc with a different ending as the same doc", () => {
    open();
    add(docUrl("same"));
    add("https://docs.google.com/document/d/same/edit?usp=sharing");
    expect(screen.getByRole("alert").textContent).toBe(w.linkAlreadyAdded);
  });

  it("adds on Enter, announces it, and gives the box focus back", () => {
    open();
    link(docUrl("abc"));
    fireEvent.submit(screen.getByLabelText(w.linkLabel).closest("form")!);
    expect(screen.getByText(w.linkName(1))).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(
      w.added(w.linkName(1), 1),
    );
    expect(document.activeElement).toBe(screen.getByLabelText(w.linkLabel));
  });

  it("sends only the links that opened, renamed by place, and keeps their dates", async () => {
    const api = open();
    add(docUrl("private-first")); // does not open
    add(docUrl("good")); // opens, and becomes "Google Doc 1" in what is sent
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.input(
      await screen.findByLabelText(w.dateLabel("Google Doc 2")),
      { target: { value: "2026-09-21" } },
    );
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    expect(api.calls).toContain("importCheck:100.cmp1|links:1");
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await waitFor(() =>
      expect(api.calls).toContain(
        `importStart:100.cmp1:1=Google Doc 1.txt@2026-09-21|links:${docUrl("good")}`,
      ),
    );
    // The DM sees the name from their own list, never the one renamed for the API, and no ".txt".
    expect(screen.queryByText("Google Doc 1.txt")).toBeNull();
    expect(screen.queryByText("Google Doc 2.txt")).toBeNull();
    expect(screen.getAllByText("Google Doc 2").length).toBeGreaterThan(0);
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
    // Files are added to the list now, so the big one is removed before the next is judged alone.
    fireEvent.click(screen.getByRole("button", { name: w.removeFile("big.txt") }));
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

describe("the Preview button", () => {
  const previewButton = () =>
    screen.getByRole("button", { name: w.preview }) as HTMLButtonElement;

  it("is blocked by a bad file, and freed again by choosing a good one", async () => {
    open();
    await pick([file("notes.docx", "A: x")]);
    expect(previewButton().disabled).toBe(true);
    await pick([file("big.txt", "A: x", 3 * 1024 * 1024)]);
    expect(previewButton().disabled).toBe(true);
    // Removing the bad files frees it again, and a good file can then be chosen.
    fireEvent.click(screen.getByRole("button", { name: w.removeFile("notes.docx") }));
    fireEvent.click(screen.getByRole("button", { name: w.removeFile("big.txt") }));
    await pick([s1]);
    expect(previewButton().disabled).toBe(false);
  });

  it("is not locked by an answer from the API, so the DM can press it again", async () => {
    const api = mockApi("table");
    let n = 0;
    api.importPreview = async (ref, files) => {
      n += 1;
      if (n === 1) throw new ApiError("too-soon");
      return mockApi("table").importPreview!(ref, files);
    };
    open(api);
    await pick([s1]);
    fireEvent.click(previewButton());
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["too-soon"],
    );
    expect(previewButton().disabled).toBe(false);
    fireEvent.click(previewButton());
    await screen.findByRole("heading", { name: w.matchHeading });
  });
});

describe("what the DM is told before importing", () => {
  it("says that later sessions will be renumbered, and uses the check's own estimate", async () => {
    const api = mockApi("table");
    const check = api.importCheck!.bind(api);
    api.importCheck = async (ref, files, mapping) => ({
      ...(await check(ref, files, mapping)),
      estimate: { sessions: 1, fastCalls: 99 },
    });
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(await screen.findByText(w.renumberNote)).toBeTruthy();
    expect(screen.getByText(w.estimate(1, 14))).toBeTruthy(); // the preview's, before Check
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    expect(await screen.findByText(w.estimate(1, 99))).toBeTruthy(); // the check's, after
  });

  it("shows a remembered person with no name as the label, not a blank option", async () => {
    const api = mockApi("table");
    const preview = api.importPreview!.bind(api);
    api.importPreview = async (ref, files) => {
      const found = await preview(ref, files);
      return {
        ...found,
        files: found.files.map((f) => ({
          ...f,
          speakers: f.speakers.map((x) =>
            x.label === "Cerric"
              ? {
                  ...x,
                  suggested: { userId: "9", name: "", why: "remembered" },
                }
              : x,
          ),
        })),
      };
    };
    api.importMembers = async () => [];
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    const cerric = (await screen.findByLabelText(
      `Cerric: ${w.matchHeading}`,
    )) as HTMLSelectElement;
    expect(cerric.selectedOptions[0]?.textContent).toBe("Cerric");
  });
});

describe("progress reasons", () => {
  it("shows the reason on a session that is done but not perfect, and a plain line for a failure with no reason", async () => {
    const api = mockApi("table");
    api.importStart = async () => ({
      importId: "x",
      sessions: [],
      estimate: { sessions: 2, fastCalls: 1 },
    });
    api.importProgress = async () => ({
      sessions: [
        {
          file: "a.txt",
          date: "2026-09-14",
          state: "done",
          reason: "partly_labelled",
        },
        { file: "b.txt", date: "2026-09-21", state: "failed", reason: null },
      ],
    });
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    expect(
      await screen.findByText(new RegExp(w.failedReason.partly_labelled ?? "")),
    ).toBeTruthy();
    expect(screen.getByText(new RegExp(w.failedFallback))).toBeTruthy();
  });
});

describe("a busy moment during Check", () => {
  it("shows the too-soon words and leaves Check usable", async () => {
    const api = mockApi("table");
    let n = 0;
    api.importCheck = async (ref, files, mapping) => {
      n += 1;
      if (n === 1) throw new ApiError("too-soon");
      return mockApi("table").importCheck!(ref, files, mapping);
    };
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      w.problem["too-soon"],
    );
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
  });

  it("does not end a progress line with a bare separator when the reason is unknown", async () => {
    const api = mockApi("table");
    api.importStart = async () => ({
      importId: "x",
      sessions: [],
      estimate: { sessions: 1, fastCalls: 1 },
    });
    api.importProgress = async () => ({
      sessions: [
        {
          file: "a.txt",
          date: "2026-09-14",
          state: "done",
          reason: "something_new",
        },
      ],
    });
    open(api);
    await pick([s1]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    const row = await screen.findByText(/2026-09-14 · Done/);
    expect(row.textContent?.trim().endsWith("·")).toBe(false);
  });
});

describe("links over the wire", () => {
  it("sends the links beside the files only when there are links", async () => {
    const seen: string[] = [];
    const real = httpApi("https://api.example.test", (async (
      _u: string,
      init: RequestInit,
    ) => {
      seen.push(String(init.body));
      return new Response(
        JSON.stringify({ files: [], estimate: { sessions: 0, fastCalls: 0 } }),
        { status: 200 },
      );
    }) as unknown as typeof fetch);
    await real.importPreview!("100.cmp1", [{ name: "a.txt", text: "A: x" }]);
    await real.importPreview!(
      "100.cmp1",
      [],
      ["https://docs.google.com/document/d/x/edit"],
    );
    expect(JSON.parse(seen[0] ?? "{}")).toEqual({
      files: [{ name: "a.txt", text: "A: x" }],
    });
    expect(JSON.parse(seen[1] ?? "{}")).toEqual({
      files: [],
      links: ["https://docs.google.com/document/d/x/edit"],
    });
  });
});

describe("more refusals", () => {
  it("has words for a file with too many lines or names, in the file list and as a refusal", async () => {
    const api = mockApi("table");
    api.importPreview = async () => ({
      files: [
        {
          name: "a.txt",
          format: "plain",
          error: "too_many_lines",
          suggestedDate: null,
          lineCount: 0,
          leftOut: 0,
          speakers: [],
        },
        {
          name: "b.txt",
          format: "plain",
          error: "too_many_speakers",
          suggestedDate: null,
          lineCount: 0,
          leftOut: 0,
          speakers: [],
        },
      ],
      estimate: { sessions: 0, fastCalls: 0 },
    });
    open(api);
    await pick([s1, s2]);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    expect(
      await screen.findByText(w.fileError.too_many_lines ?? ""),
    ).toBeTruthy();
    expect(screen.getByText(w.fileError.too_many_speakers ?? "")).toBeTruthy();
    expect(w.problem["too-soon"]).toBe("Wait a minute, then try again.");
    for (const kind of ["too-many-lines", "too-many-speakers"])
      expect(w.problem[kind], kind).toBeTruthy();
  });

  it("reads the new codes over the wire", async () => {
    const real = (status: number, detail: string) =>
      httpApi(
        "https://api.example.test",
        (async () =>
          new Response(JSON.stringify({ detail }), {
            status,
          })) as unknown as typeof fetch,
      ).importPreview!("100.cmp1", []);
    await expect(real(429, "too_soon")).rejects.toMatchObject({
      kind: "too-soon",
    });
    await expect(real(413, "too_many_lines")).rejects.toMatchObject({
      kind: "too-many-lines",
    });
    await expect(real(413, "too_many_speakers")).rejects.toMatchObject({
      kind: "too-many-speakers",
    });
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

  it("says the session ended, and leaves the one Sign in button to the shared line", async () => {
    await refused(new ApiError("signed-out"));
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
    // The page's own panel only says it; the one Sign in button is the shared line's (#2301).
    expect(screen.queryByRole("link", { name: w.signIn })).toBeNull();
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

describe("the chosen files can be managed (#2303)", () => {
  const names = () =>
    within(screen.getByRole("list", { name: w.filesListLabel }))
      .getAllByRole("listitem")
      .map((li) => li.querySelector(".campaign-name")?.textContent);
  const previewButton = () => screen.getByRole("button", { name: w.preview }) as HTMLButtonElement;

  it("lists each chosen file on its own row, and choosing more adds to the list", async () => {
    open();
    await pick([s1]);
    expect(names()).toEqual([w.shownName(s1.name)]);
    await pick([s2]);
    expect(names()).toEqual([w.shownName(s1.name), w.shownName(s2.name)]);
    expect(screen.getByText(w.chosen(2))).toBeTruthy();
  });

  it("skips a file that is already on the list, and says so", async () => {
    open();
    await pick([s1]);
    await pick([s1]);
    expect(names()).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe(w.alreadyChosen);
    await pick([s1, s2]);
    expect(names()).toHaveLength(2);
  });

  it("removes one file, leaving the others", async () => {
    open();
    await pick([s1, s2]);
    fireEvent.click(screen.getByRole("button", { name: w.removeFile(s1.name) }));
    expect(names()).toEqual([w.shownName(s2.name)]);
    expect(screen.getByRole("status").textContent).toBe(w.removed(s1.name, 1));
  });

  it("lets a removed file be chosen again", async () => {
    open();
    const input = (await pick([s1])) as HTMLInputElement;
    // The page clears the picker so the same file can be chosen again. A fresh input has no value,
    // so give it one the way a browser does after a choice, and see the next choice clear it.
    Object.defineProperty(input, "value", { value: "C:\\fakepath\\x.txt", writable: true, configurable: true });
    await pick([s2]);
    expect(input.value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: w.removeFile(s1.name) }));
    fireEvent.click(screen.getByRole("button", { name: w.removeFile(s2.name) }));
    expect(screen.queryByRole("list", { name: w.filesListLabel })).toBeNull();
    await pick([s1]);
    expect(names()).toEqual([w.shownName(s1.name)]);
  });

  it("Remove all clears the files and the links together", async () => {
    open();
    await pick([s1, s2]);
    fireEvent.input(screen.getByLabelText(w.linkLabel), {
      target: { value: "https://docs.google.com/document/d/abc123/edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: w.addLink }));
    fireEvent.click(screen.getByRole("button", { name: w.removeAll }));
    expect(screen.queryByRole("list", { name: w.filesListLabel })).toBeNull();
    expect(screen.queryByText(w.linkName(1))).toBeNull();
    expect(screen.getByRole("status").textContent).toBe(w.removedAll);
    expect(previewButton().disabled).toBe(true);
  });

  it("a removed file takes its result row, and the rest of the Preview, with it", async () => {
    open();
    await pick([file("session-unknown.txt", "xyz"), s1]);
    fireEvent.click(previewButton());
    expect(await screen.findByText(w.fileError.unknown_format ?? "")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.removeFile("session-unknown.txt") }));
    expect(screen.queryByText(w.fileError.unknown_format ?? "")).toBeNull();
    expect(screen.queryByRole("heading", { name: w.matchHeading })).toBeNull();
    expect(names()).toEqual([w.shownName(s1.name)]);
  });

  it("says why a file couldn't be read, in plain words", async () => {
    open();
    await pick([file("a-unknown.txt", "xyz"), file("b-nonames.txt", "xyz"), s1]);
    fireEvent.click(previewButton());
    expect(await screen.findByText(w.fileError.unknown_format ?? "")).toBeTruthy();
    expect(screen.getByText("This caption file has no names in it, so DMbot can't tell who spoke.")).toBeTruthy();
    expect(w.fileError.unknown_format).toContain("DMbot's own download");
  });

  it("shows no Remove all for a single item", async () => {
    open();
    await pick([s1]);
    expect(screen.queryByRole("button", { name: w.removeAll })).toBeNull();
  });
  it("moves focus to the next Remove after one is pressed, then the previous one, then the picker", async () => {
    open();
    await pick([s1, s2, file("c.txt", "Mira: hi")]);
    const remove = (name: string) => screen.getByRole("button", { name: w.removeFile(name) });
    fireEvent.click(remove(s2.name));
    await waitFor(() => expect(document.activeElement).toBe(remove("c.txt")));
    fireEvent.click(remove("c.txt"));
    await waitFor(() => expect(document.activeElement).toBe(remove(s1.name)));
    fireEvent.click(remove(s1.name));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText(w.pickLabel, { selector: "input" })),
    );
  });

  it("counts files and links together against the limit again after a Remove", async () => {
    open();
    const many = Array.from({ length: IMPORT_MAX_FILES + 1 }, (_, i) => file(`n${i}.txt`, "A: x"));
    await pick(many);
    expect(screen.getByRole("alert").textContent).toBe(w.tooManyFiles);
    expect(previewButton().disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: w.removeFile("n0.txt") }));
    expect(screen.queryByText(w.tooManyFiles)).toBeNull();
    expect(previewButton().disabled).toBe(false);
  });

  it("says on the screen, and aloud, which choices were left off, and what was added", async () => {
    open();
    await pick([s1]);
    await pick([s1, s2]);
    // One was new and one was already there: both are said, the second on the screen too.
    expect(screen.getByRole("status").textContent).toBe(`${w.addedFiles(1, 2)} ${w.alreadyChosen}`);
    expect(screen.getByText(w.alreadyChosen)).toBeTruthy(); // the on-screen line, exactly
    fireEvent.click(screen.getByRole("button", { name: w.removeFile(s2.name) }));
    expect(screen.queryByText(w.alreadyChosen)).toBeNull();
  });
});

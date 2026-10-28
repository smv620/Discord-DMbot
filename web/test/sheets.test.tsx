/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Character sheets" on a campaign's page (#2816): a player sees and links or forgets their own characters' sheets, or types one in (#2832); a DM
// sees every sheet read only and asks for them to be read again. Against the pretend API, and only while SHEETS_LIVE is
// on (false today), so nothing is asked of the API before then.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import CharacterSheetsSection from "../src/account/Sheets";
import { sheetWords as w } from "../src/content/sheets-words";

afterEach(cleanup);

const calls = (api: ReturnType<typeof mockApi>, start: string) =>
  api.calls.filter((c) => c.startsWith(start));
async function open(ending = "") {
  const api = mockApi("table");
  render(<CharacterSheetsSection api={api} campaignRef={`100.sh${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("the player's view", () => {
  it("shows their own character with its link, and an unlinked one with a form to link it", async () => {
    await open();
    expect(screen.getByText(w.mine("Sorrel"))).toBeTruthy();
    expect(screen.getByText(w.onlyYou, { exact: false })).toBeTruthy();
    expect(
      screen.getByRole("link", {
        name: "https://www.dndbeyond.com/characters/12345",
      }),
    ).toBeTruthy();
    expect(screen.getByText(w.summary(3, "Wizard 3"))).toBeTruthy();
    expect(screen.getAllByText("Ivy", { exact: false }).length).toBeGreaterThan(
      0,
    );
  });

  it("links a sheet: the link goes to the bot queue, and the saved words show once it is done", async () => {
    const api = await open();
    const box = screen.getAllByRole("textbox")[0] as HTMLInputElement;
    fireEvent.input(box, {
      target: { value: "https://www.dndbeyond.com/characters/777" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
    expect(
      await screen.findByText(w.linkSaved, {}, { timeout: 8000 }),
    ).toBeTruthy();
    expect(calls(api, "chooseSheet")[0]).toContain('"action":"link"');
  });

  it("says plainly that a link which isn't a character link was refused, and sends nothing", async () => {
    const api = await open();
    fireEvent.input(screen.getAllByRole("textbox")[0] as HTMLInputElement, {
      target: { value: "https://example.com/x" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
    expect(
      await screen.findByText(
        w.problems["bad-link"] as string,
        {},
        { timeout: 8000 },
      ),
    ).toBeTruthy();
    expect(screen.queryByText(w.linkSaved)).toBeNull(); // nothing was linked
    expect(calls(api, "chooseSheet")).toHaveLength(1); // the API refused it, once
  });

  it("forgets their sheet", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: w.forget }));
    expect(
      await screen.findByText(w.forgetDone, {}, { timeout: 8000 }),
    ).toBeTruthy();
  });
});

describe("the DM's view", () => {
  it("shows every sheet read only: no link form, and a read again button", async () => {
    await open(".sheets-dm");
    expect(screen.getByText(w.dmOnly)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.forget })).toBeNull();
    expect(screen.getByRole("button", { name: w.readAgain })).toBeTruthy();
  });

  it("asks for the sheets to be read again, once", async () => {
    const api = await open(".sheets-dm");
    fireEvent.click(screen.getByRole("button", { name: w.readAgain }));
    expect(
      await screen.findByText(w.readAgainDone, {}, { timeout: 8000 }),
    ).toBeTruthy();
    expect(calls(api, "chooseSheet")[0]).toContain('"action":"read_again"');
  });

  it("says when the minute has not passed yet", async () => {
    await open(".sheets-dm.sheets-slow");
    fireEvent.click(screen.getByRole("button", { name: w.readAgain }));
    expect(
      await screen.findByText(
        w.problems["too-soon"] as string,
        {},
        { timeout: 8000 },
      ),
    ).toBeTruthy();
  });
});

describe("nothing for someone who may not see the campaign", () => {
  it("shows the failure words, not the sheets, for a campaign that isn't theirs", async () => {
    const api = mockApi("table");
    render(
      <CharacterSheetsSection api={api} campaignRef="100.sh.sheets-none" />,
    );
    expect(await screen.findByText(w.failedToLoad)).toBeTruthy();
    expect(screen.queryByText("Sorrel")).toBeNull();
  });
});

describe("the wait", () => {
  it("shows what DMbot is doing while the answer is still waiting, and says so when it stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await open(".sheets-stuck");
      fireEvent.input(screen.getAllByRole("textbox")[0] as HTMLInputElement, {
        target: { value: "https://www.dndbeyond.com/characters/9" },
      });
      fireEvent.click(screen.getAllByRole("button", { name: w.link })[0]!);
      expect(
        await screen.findByText(
          w.stage.waiting as string,
          {},
          { timeout: 8000 },
        ),
      ).toBeTruthy();
      await waitFor(() =>
        expect(screen.queryByText(w.stage.waiting as string)).toBeTruthy(),
      );
      // Nothing moves on the bot's side for a minute: the panel must say so, not keep saying "running".
      await vi.advanceTimersByTimeAsync(61_000);
      expect(
        await screen.findByText(w.quiet, {}, { timeout: 8000 }),
      ).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Type in my character", () => {
  // Sorrel (first) has a sheet linked; Ivy (second) has none. The player's list is in that order.
  const fill = (index: number, values: { className?: string; level?: string; species?: string; names?: string }) => {
    if (values.className !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedClass)[index]!, { target: { value: values.className } });
    if (values.level !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedLevel)[index]!, { target: { value: values.level } });
    if (values.species !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedSpecies)[index]!, { target: { value: values.species } });
    if (values.names !== undefined) fireEvent.input(screen.getAllByLabelText(w.typedNames)[index]!, { target: { value: values.names } });
  };
  // Submitted as a form: a click on the button inside the closed "Type in my character" panel does not submit here.
  const save = (index: number) => fireEvent.submit(screen.getAllByLabelText(w.typedClass)[index]!.closest("form")!);
  const typedCalls = (api: ReturnType<typeof mockApi>) => calls(api, "chooseSheet").filter((c) => c.includes('"action":"typed"'));

  it("offers the form to the player and not to the DM", async () => {
    await open();
    expect(screen.getAllByText(w.typedSummary).length).toBeGreaterThan(0);
    expect(screen.getByText(w.typedFor("Sorrel"))).toBeTruthy();
    cleanup();
    await open(".sheets-dm");
    expect(screen.queryByText(w.typedSummary)).toBeNull();
    expect(screen.queryByLabelText(w.typedClass)).toBeNull();
  });

  it("sends what was typed, trimmed, with the level as a number", async () => {
    const api = await open();
    fill(1, { className: "  Wizard ", level: " 5 ", species: " Elf ", names: "Misty Step, Shield" });
    save(1);
    expect(await screen.findByText(w.typedSaved, {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls).toContain(
      `chooseSheet:100.sh:sh-ivy:${JSON.stringify({ action: "typed", className: "Wizard", level: 5, species: "Elf", names: "Misty Step, Shield" })}`,
    );
  });

  // Each case opens a fresh page, so one case's message cannot be the one that another case finds.
  it.each(["0", "21", "x", "1.5"])("refuses the level %s and sends nothing", async (level) => {
    const api = await open();
    fill(1, { className: "Wizard", level });
    save(1);
    expect(await screen.findByText(w.typedBadLevel)).toBeTruthy();
    expect(typedCalls(api)).toHaveLength(0);
  });

  it.each(["1", "20"])("accepts the level %s as a boundary and sends it as a number", async (level) => {
    const api = await open();
    fill(1, { className: "Wizard", level });
    save(1);
    expect(await screen.findByText(w.typedSaved, {}, { timeout: 8000 })).toBeTruthy();
    expect(typedCalls(api).some((c) => c.includes(`"level":${level},`))).toBe(true);
  });

  it("keeps a half-typed draft across a re-render, and never shows it under another campaign", async () => {
    const api = mockApi("table");
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.input(screen.getAllByLabelText(w.typedClass)[1]!, { target: { value: "Wizard" } });
    rerender(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    expect((screen.getAllByLabelText(w.typedClass)[1] as HTMLInputElement).value).toBe("Wizard");
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByRole("heading", { name: w.heading });
    expect((screen.getAllByLabelText(w.typedClass)[1] as HTMLInputElement).value).toBe("");
  });

  it("refuses a blank class and sends nothing", async () => {
    const api = await open();
    fill(1, { className: "   ", level: "5" });
    save(1);
    expect(await screen.findByText(w.typedNeedsClass)).toBeTruthy();
    expect(typedCalls(api)).toHaveLength(0);
  });

  it("tells a player with a linked sheet that saving replaces the link", async () => {
    await open();
    expect(screen.getAllByText(w.typedReplacesLink)).toHaveLength(1);
    fill(0, { className: "Wizard", level: "5" });
    save(0);
    expect(await screen.findByText(w.typedSaved + w.typedReplacedLink, {}, { timeout: 8000 })).toBeTruthy();
  });
});

describe("a link typed for one campaign", () => {
  it("does not show under another campaign's character, and is empty again on return", async () => {
    const api = mockApi("table");
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    const linkBox = () => screen.getAllByRole("textbox")[0] as HTMLInputElement;
    fireEvent.input(linkBox(), { target: { value: "https://www.dndbeyond.com/characters/777" } });
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await waitFor(() => expect(linkBox().value).toBe(""));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await waitFor(() => expect(linkBox().value).toBe(""));
  });
});

describe("a campaign the page has left", () => {
  it("an old campaign's slow read is not drawn after a switch", async () => {
    const api = mockApi("table");
    const fetch = api.campaignSheets!.bind(api);
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    api.campaignSheets = async (ref) => {
      const got = await fetch(ref);
      if (ref !== "100.sh") return got;
      await held;
      return { ...got, sheets: got.sheets.map((c) => ({ ...c, name: "Stale Oren" })) };
    };
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    release();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale Oren", { exact: false })).toBeNull();
    expect(screen.getByText(w.mine("Sorrel"))).toBeTruthy();
  });

  it("an old campaign's failed read does not show the failure on the new one", async () => {
    const api = mockApi("table");
    const fetch = api.campaignSheets!.bind(api);
    let fail!: () => void;
    const held = new Promise<void>((r) => (fail = r));
    api.campaignSheets = async (ref) => {
      if (ref !== "100.sh") return fetch(ref);
      await held;
      throw new ApiError("server");
    };
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    fail();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(w.failedToLoad)).toBeNull();
    expect(screen.getByText(w.mine("Sorrel"))).toBeTruthy();
  });

  it("an answer for a choice made in the old campaign shows no report in the new one", async () => {
    const api = mockApi("table");
    const answer = api.sheetAnswer!.bind(api);
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    api.sheetAnswer = async (ref, id) => {
      if (ref !== "100.sh") return answer(ref, id);
      await held;
      return answer(ref, id);
    };
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.forget }));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    // The old section stops asking once the page has left (#2851), so no answer is ever drawn here.
    await new Promise((r) => setTimeout(r, 2000));
    release();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(w.forgetDone)).toBeNull();
  });
});

describe("Use a document link", () => {
  const docBox = (name: string) => screen.getByLabelText(w.documentLabel(name)) as HTMLInputElement;
  const sendDocument = (name: string) => fireEvent.submit(docBox(name).closest("form")!);
  const documentCalls = (api: ReturnType<typeof mockApi>) => calls(api, "chooseSheet").filter((c) => c.includes('"action":"document"'));

  it("a player sends a document link and the choice is queued as document", async () => {
    const api = await open();
    fireEvent.input(docBox("Ivy"), { target: { value: "https://example.com/sheet.pdf" } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.documentSent, {}, { timeout: 8000 })).toBeTruthy();
    expect(api.calls).toContain(`chooseSheet:100.sh:sh-ivy:${JSON.stringify({ action: "document", url: "https://example.com/sheet.pdf" })}`);
  });

  // Each case opens a fresh page, so one case's message cannot be the one another case finds.
  it.each([
    ["", "empty"],
    ["a word with spaces", "not-web"],
    ["ftp://example.com/x", "not-web"],
    ["http://example.com/x", "not-web"],
  ] as const)("a link of the wrong shape (%j) sends nothing and says why", async (text, problem) => {
    const api = await open();
    fireEvent.input(docBox("Ivy"), { target: { value: text } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.documentProblems[problem]!)).toBeTruthy();
    expect(documentCalls(api)).toHaveLength(0);
  });

  it("an upper-case https link is sent, and the mock reads it as a web address", async () => {
    const api = await open();
    fireEvent.input(docBox("Ivy"), { target: { value: "HTTPS://example.com/sheet.pdf" } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.documentSent, {}, { timeout: 8000 })).toBeTruthy();
    expect(documentCalls(api)).toHaveLength(1);
    expect(api.calls).toContain(`chooseSheet:100.sh:sh-ivy:${JSON.stringify({ action: "document", url: "https://example.com/sheet.pdf" })}`);
  });

  it("the mock refuses a link it can't read with bad-document-link, in the document's own words", async () => {
    const api = await open();
    // Passes the page's check (a literal prefix and a host), but the mock reads no host after the slashes.
    fireEvent.input(docBox("Ivy"), { target: { value: "https:///example.com" } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.problems["bad-document-link"] as string, {}, { timeout: 8000 })).toBeTruthy();
    expect(documentCalls(api)).toHaveLength(1);
  });

  it("a link longer than 400 characters is refused with the plain words, not cut short", async () => {
    const api = await open();
    const long = "https://example.com/" + "a".repeat(401 - "https://example.com/".length);
    fireEvent.input(docBox("Ivy"), { target: { value: long } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.documentProblems["too-long"]!)).toBeTruthy();
    expect(documentCalls(api)).toHaveLength(0);
  });

  it("a DM does not see the document form", async () => {
    await open(".sheets-dm");
    expect(screen.queryByText(w.documentSummary)).toBeNull();
    expect(screen.queryByLabelText(w.documentLabel("Sorrel"))).toBeNull();
  });

  it("an existing sheet shows that reading a document replaces it", async () => {
    await open();
    expect(screen.getAllByText(w.documentReplaces)).toHaveLength(1);
  });

  it("the document draft is cleared after a successful send", async () => {
    await open();
    fireEvent.input(docBox("Ivy"), { target: { value: "https://example.com/sheet.pdf" } });
    sendDocument("Ivy");
    expect(await screen.findByText(w.documentSent, {}, { timeout: 8000 })).toBeTruthy();
    expect(docBox("Ivy").value).toBe("");
  });

  it("a half-typed document link does not show under another campaign", async () => {
    const api = mockApi("table");
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.input(docBox("Ivy"), { target: { value: "https://example.com/half" } });
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await waitFor(() => expect(docBox("Ivy").value).toBe(""));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await waitFor(() => expect(docBox("Ivy").value).toBe(""));
  });

  it("a choice still going on one campaign does not disable another campaign's controls", async () => {
    const api = mockApi("table");
    const answer = api.sheetAnswer!.bind(api);
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    api.sheetAnswer = async (ref, id) => {
      if (ref === "100.sh") await held;
      return answer(ref, id);
    };
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.forget }));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    const forget = screen.getByRole("button", { name: w.forget });
    expect(forget.getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.queryByText(w.stage.waiting as string)).toBeNull();
    fireEvent.click(forget);
    await waitFor(() =>
      expect(api.calls).toContain(`chooseSheet:100.other:sh-sorrel:${JSON.stringify({ action: "forget" })}`),
    );
    release();
  });

  it("a read from an earlier visit cannot overwrite a later visit", async () => {
    const api = mockApi("table");
    const real = api.campaignSheets!.bind(api);
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    let first = true;
    api.campaignSheets = async (ref) => {
      const got = await real(ref);
      if (ref !== "100.sh" || !first) return got;
      first = false;
      await held;
      return { ...got, sheets: got.sheets.map((c) => ({ ...c, name: "Stale Oren" })) };
    };
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByText(w.mine("Sorrel"));
    release();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale Oren", { exact: false })).toBeNull();
    expect(screen.getByText(w.mine("Sorrel"))).toBeTruthy();
  });

  it("the old section stops asking about its choice once the page has left", async () => {
    const api = mockApi("table");
    const { rerender } = render(<CharacterSheetsSection api={api} campaignRef="100.sh" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: w.forget }));
    rerender(<CharacterSheetsSection api={api} campaignRef="100.other" />);
    await screen.findByText(w.mine("Sorrel"));
    const asked = () => api.calls.filter((c) => c.startsWith("sheetAnswer:100.sh")).length;
    const before = asked();
    await new Promise((r) => setTimeout(r, 2000));
    expect(asked()).toBe(before);
  });
});

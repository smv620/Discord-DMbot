/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The House-rules file on the website (#2985, first slice): only a game master sees it, a link is never shown back
// (only its site), a compare shows counts and always says where to go next, and a failure says what to do.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import HouseFilePanel from "../src/account/HouseFilePanel";
import { HOUSE_FILE_LIVE } from "../src/content/house-file-switch";
import { discordPath, houseFilePanel as words } from "../src/content/house-file-words";
import { entries, held } from "../src/content/whats-new";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const TITLE = "Connect your house-rules file on the website";
const LINK = "https://docs.example.com/document/d/secret-token-123/edit";

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
}

async function connect(api: ReturnType<typeof mockApi>, ref = "100.cmp1"): Promise<void> {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<HouseFilePanel api={api} campaignRef={ref} />);
  fireEvent.click(await screen.findByRole("button", { name: words.connect }));
  fireEvent.input(screen.getByLabelText(words.linkLabel), { target: { value: LINK } });
  fireEvent.click(screen.getByRole("button", { name: words.linkSend }));
}

describe("The House-rules file on the website", () => {
  it("holds its What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === TITLE)).toBe(!HOUSE_FILE_LIVE);
    expect(entries.some((e) => e.title === TITLE)).toBe(HOUSE_FILE_LIVE);
  });

  it("shows nothing at all to someone who is not a game master, or while the switch is off", async () => {
    for (const ref of ["100.cmp1.hf-player", "100.cmp1.hf-none"]) {
      const api = mockApi("table");
      const { container, unmount } = render(<HouseFilePanel api={api} campaignRef={ref} />);
      await waitFor(() => expect(api.calls).toContain(`houseFile:${ref}:-`));
      await waitFor(() => expect(container.textContent).toBe(""));
      unmount();
    }
  });

  it("starts with no file connected and offers to connect one or upload one", async () => {
    render(<HouseFilePanel api={mockApi("table")} campaignRef="100.cmp1" />);
    expect(await screen.findByText(words.notConnected)).toBeTruthy();
    expect(screen.getByRole("button", { name: words.connect })).toBeTruthy();
    expect(screen.getByRole("button", { name: words.upload })).toBeTruthy();
    expect(screen.queryByRole("button", { name: words.compare })).toBeNull();
    expect(screen.queryByRole("button", { name: words.stop })).toBeNull();
  });

  it("connects a file, follows the request and shows the site, never the link", async () => {
    const api = mockApi("table");
    await connect(api);
    await waitFor(() => expect(api.calls).toContain(`houseFileAdd:100.cmp1:link:${LINK.length}`));
    await settle();
    expect(await screen.findByText(words.connected("docs.example.com"))).toBeTruthy();
    expect(document.body.textContent).not.toContain("secret-token");
    expect(document.body.textContent).not.toContain(LINK);
  });

  it("a compare with differences shows the counts and the real Discord path", async () => {
    const api = mockApi("table");
    await connect(api);
    await settle();
    expect(await screen.findByText(words.counts(2, 1, 3))).toBeTruthy();
    expect(screen.getByText(words.next)).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toContain("⚙️ Manage DMbot");
    expect(text).toContain("📄 House-rules file");
    expect(text).toContain("🔄 Compare now");
    expect(text).toContain("Accept all changes");
    expect(text).toContain("Review");
  });

  it("a compare with no differences says so and needs no next step", async () => {
    const api = mockApi("table");
    await connect(api, "100.cmp1.hf-match");
    await settle();
    expect(await screen.findByText(words.matches)).toBeTruthy();
    expect(screen.queryByText(words.next)).toBeNull();
  });

  it("Compare now sends a check once a file is connected", async () => {
    const api = mockApi("table");
    await connect(api);
    await settle();
    fireEvent.click(await screen.findByRole("button", { name: words.compare }));
    await waitFor(() => expect(api.calls).toContain("houseFileAdd:100.cmp1:check:0"));
  });

  it("a file that can't be read says what happened and what to do next", async () => {
    const api = mockApi("table");
    await connect(api, "100.cmp1.hf-fail");
    await settle();
    expect(await screen.findByText(new RegExp(words.failedReasons.unreadable!))).toBeTruthy();
    expect(document.body.textContent).toContain("📄 House-rules file");
  });

  it("refuses a link that is not https before anything is queued", async () => {
    const api = mockApi("table");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: words.connect }));
    fireEvent.input(screen.getByLabelText(words.linkLabel), { target: { value: "http://docs.example.com/plain" } });
    fireEvent.click(screen.getByRole("button", { name: words.linkSend }));
    expect(await screen.findByText(words.problems["bad-text"]!)).toBeTruthy();
  });

  it("asks before it stops using the file, and sends nothing on Keep it", async () => {
    const api = mockApi("table");
    await connect(api);
    await settle();
    fireEvent.click(await screen.findByRole("button", { name: words.stop }));
    expect(screen.getByText(words.stopAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.keep }));
    expect(api.calls.some((c) => c.includes(":unlink:"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: words.stop }));
    fireEvent.click(screen.getByRole("button", { name: words.stopYes }));
    await waitFor(() => expect(api.calls).toContain("houseFileAdd:100.cmp1:unlink:0"));
    await settle();
    expect(await screen.findByText(words.results.unlink!)).toBeTruthy();
    expect(await screen.findByText(words.notConnected)).toBeTruthy();
  });

  it("uploads a text file once and never shows its words", async () => {
    const api = mockApi("table");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: words.upload }));
    const file = new File(["1. Flanking gives advantage"], "rules.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(words.upload), { target: { files: [file] } });
    await screen.findByText("rules.txt");
    fireEvent.click(screen.getByRole("button", { name: words.uploadSend }));
    await waitFor(() => expect(api.calls).toContain("houseFileAdd:100.cmp1:upload:27"));
    await settle();
    expect(document.body.textContent).not.toContain("Flanking");
  });

  it("refuses a file that is not plain text", async () => {
    const api = mockApi("table");
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: words.upload }));
    const file = new File(["PK\u0000\u0003"], "rules.docx");
    fireEvent.change(screen.getByLabelText(words.upload), { target: { files: [file] } });
    expect(await screen.findByText(words.fileUnreadable)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("houseFileAdd"))).toBe(false);
  });

  it("shows the open request instead of a second one", async () => {
    const api = mockApi("table");
    await connect(api);
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("houseFileAdd")).length).toBe(1));
    // (the buttons are off while one is open)
    expect((screen.getByRole("button", { name: words.connect }) as HTMLButtonElement).disabled).toBe(true); // (not connected until DMbot has done it)
    expect((screen.getByRole("button", { name: words.upload }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows only the counts that are not zero, and warns about removals", async () => {
    expect(words.counts(1, 0, 3)).toBe("1 to add, 3 to remove.");
    expect(words.counts(0, 2, 0)).toBe("2 to change.");
    const api = mockApi("table");
    await connect(api);
    await settle();
    expect(await screen.findByText(words.removing)).toBeTruthy(); // (the pretend compare has 3 to remove)
  });

  it("the Discord path names a button that is really there for what was asked", () => {
    // 🔄 Compare now only exists when a file is connected (ui/house_file.py), so it is never offered otherwise.
    expect(discordPath("check", true)).toContain("🔄 Compare now");
    expect(discordPath("link", true)).toContain("🔄 Compare now");
    expect(discordPath("check", false)).toContain("🔗 Connect a file");
    expect(discordPath("link", false)).toContain("🔗 Connect a file");
    expect(discordPath("upload", false)).toContain("📎 Upload a file once");
    expect(discordPath("upload", true)).toContain("📎 Upload a file once");
    expect(discordPath("unlink", true)).toContain("Stop using this file");
    for (const action of ["check", "link", "upload", "unlink"]) {
      for (const linked of [true, false]) {
        const line = discordPath(action, linked);
        expect(line).toContain("⚙️ Manage DMbot");
        expect(line).toContain("📄 House-rules file");
        if (!linked) expect(line).not.toContain("🔄 Compare now");
        expect(words.stalled(action, linked)).toContain(line);
        expect(words.failedNext(action, linked)).toContain(line);
      }
    }
  });

  it("an upload with differences and no connected file points to the upload button, not Compare now", async () => {
    const api = mockApi("table");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: words.upload }));
    fireEvent.change(screen.getByLabelText(words.upload), { target: { files: [new File(["1. A rule"], "rules.txt", { type: "text/plain" })] } });
    await screen.findByText("rules.txt");
    fireEvent.click(screen.getByRole("button", { name: words.uploadSend }));
    await settle();
    expect(await screen.findByText(words.counts(2, 1, 3))).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toContain("📎 Upload a file once");
    expect(text).not.toContain("🔄 Compare now");
  });

  it("a connect that fails points to Connect a file, since nothing is connected", async () => {
    const api = mockApi("table");
    await connect(api, "100.cmp1.hf-fail");
    await settle();
    expect(await screen.findByText(new RegExp(words.failedReasons.unreadable!))).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toContain("🔗 Connect a file");
    expect(text).not.toContain("🔄 Compare now");
    expect(await screen.findByText(words.notConnected)).toBeTruthy();
  });

  it("says just Connected when the site cannot be read from a link set in Discord", async () => {
    const api = mockApi("table");
    const real = api.houseFile!.bind(api);
    api.houseFile = async (ref, id) => ({ ...(await real(ref, id)), linked: true, site: "" });
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    expect(await screen.findByText(words.connected(""))).toBeTruthy();
    expect(words.connected("")).not.toContain(": .");
  });

  it("uses Discord's own button words and says when Accept all is off", async () => {
    expect(words.next).toContain("✅ Accept all changes");
    expect(words.next).toContain("🔍 Review");
    expect(words.next).toContain("🙈 Not now");
    const api = mockApi("table");
    await connect(api);
    await settle();
    expect(await screen.findByText(words.manyRemovals)).toBeTruthy(); // (the pretend compare has removals)
  });

  it("every failure the bot can report has plain words, and none blames DMbot's side", () => {
    for (const code of ["not_taken", "unreadable", "bad_link", "too_big", "no_rules", "busy", "too_often", "daily_limit", "no_ai", "no_offer", "not_dm"]) {
      expect(words.failedReasons[code], code).toBeTruthy();
    }
    expect(words.failedFallback).not.toMatch(/went wrong/i);
  });

  it("says so when the first look fails, and Try again looks again", async () => {
    const api = mockApi("table");
    let tries = 0;
    const real = api.houseFile!.bind(api);
    api.houseFile = async (ref, id) => {
      tries += 1;
      if (tries === 1) throw new ApiError("network");
      return real(ref, id);
    };
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    expect(await screen.findByText(words.loadFailed)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.tryAgain }));
    expect(await screen.findByText(words.notConnected)).toBeTruthy();
  });

  it("the connect and upload forms say Cancel, and Cancel sends nothing", async () => {
    const api = mockApi("table");
    render(<HouseFilePanel api={api} campaignRef="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: words.connect }));
    fireEvent.click(screen.getByRole("button", { name: words.cancel }));
    fireEvent.click(screen.getByRole("button", { name: words.upload }));
    fireEvent.click(screen.getByRole("button", { name: words.cancel }));
    expect(api.calls.some((c) => c.startsWith("houseFileAdd"))).toBe(false);
  });

  it("every result that has differences also names the next step", () => {
    expect(words.next).toContain("Accept all");
    expect(words.next).toContain("Review");
    expect(words.nextFallback("check", true)).toContain("📄 House-rules file");
  });
});

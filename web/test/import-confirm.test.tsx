import "preact/debug"; // the duplicate-key warning lives in the debug build only (#3323)
/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Confirming consent per speaker on the Import page (#3306): the consent words come from the API, a speaker needs a
// choice, a member who said no is greyed out, a ticked "not in Discord" speaker is kept by name, and the API's refusals
// (even with the page bypassed) show plain words. With IMPORT_CONFIRM_LIVE off the old choices stay.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import { confirmWords as c, importPage as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ on: false, confirm: true }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.on;
    },
    get IMPORT_CONFIRM_LIVE() {
      return live.confirm;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
  live.confirm = true;
});

const path = "/account/import/100.cmp1";
const s1 = new File(["Cerric: hello\nAsh: hi\nMira: ok\nDax: no\nOld Pip: hm"], "session-1-2026-09-14.txt", { type: "text/plain" });

async function previewed(api = mockApi("table")) {
  live.on = true;
  render(<Import api={api} path={path} />);
  const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [s1], configurable: true });
  fireEvent.change(input);
  fireEvent.click(screen.getByRole("button", { name: w.preview }));
  await screen.findByRole("heading", { name: w.matchHeading });
  return api;
}
const pickFor = (label: string): HTMLSelectElement => screen.getByLabelText(`${label}: ${w.matchHeading}`) as HTMLSelectElement;
const choose = (label: string, value: string): void => void fireEvent.change(pickFor(label), { target: { value } });

describe("confirming consent per speaker", () => {
  it("shows the consent words the API gives, above the names", async () => {
    const api = await previewed();
    expect(await screen.findByText("Everyone in the Discord server can read the transcripts.")).toBeTruthy();
    expect(screen.getByText(c.lead)).toBeTruthy();
    expect(api.calls).toContain("importConsent:100.cmp1");
  });

  it("makes the uploader choose for a name nobody suggested, and Check waits for it", async () => {
    await previewed();
    expect(pickFor("Ash").value).toBe("");
    expect((screen.getByRole("button", { name: w.checkButton }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(c.chooseAll)).toBeTruthy();
  });

  it("greys out a member who said no, with the reason when one is chosen", async () => {
    await previewed();
    const option = within(pickFor("Ash")).getByText(c.blockedOption("Dax")) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
  });

  it("asks for a tick for a speaker not in Discord, and the tick is sent with the wording's version", async () => {
    const api = await previewed();
    for (const label of ["Ash", "Mira", "Dax"]) choose(label, "drop");
    choose("Old Pip", "new");
    expect(screen.getByText(c.sameAs("Old Pip"))).toBeTruthy();
    expect((screen.getByRole("button", { name: w.checkButton }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(c.tick("Old Pip")));
    const check = screen.getByRole("button", { name: w.checkButton });
    await waitFor(() => expect((check as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(check);
    await screen.findByText(new RegExp(c.keptConfirmed));
    expect(api.calls.some((x) => x.includes("|confirmed:Old Pip@7"))).toBe(true);
  });

  it("shows the server's own tick words with the name filled in, once it sends them (#3305)", async () => {
    const api = mockApi("table");
    api.importConsent = async () => ({ version: 7, text: "Their words.", tick: "{name} agreed to the uploader's words." });
    await previewed(api);
    choose("Ash", "2");
    expect(screen.getByLabelText("Ash agreed to the uploader's words.")).toBeTruthy();
    expect(screen.queryByLabelText(c.tick("Ash"))).toBeNull();
  });

  it("asks for a tick for a member who has not agreed in Discord, naming the member", async () => {
    await previewed();
    choose("Ash", "2");
    expect(screen.getByLabelText(c.tick("Ash"))).toBeTruthy();
    expect(screen.queryByText(w.droppedReason.not_agreed!)).toBeNull();
  });

  it("clears a tick when the choice changes", async () => {
    await previewed();
    choose("Old Pip", "new");
    fireEvent.click(screen.getByLabelText(c.tick("Old Pip")));
    choose("Old Pip", "drop");
    choose("Old Pip", "new");
    expect((screen.getByLabelText(c.tick("Old Pip")) as HTMLInputElement).checked).toBe(false);
  });

  it("says in plain words when the API refuses a member who said no, even with the page bypassed", async () => {
    const api = await previewed();
    for (const label of ["Ash", "Mira", "Old Pip"]) choose(label, "drop");
    // The option is disabled on the page; a bypass is a mapping the page would not build, so ask the API directly.
    await expect(api.importCheck!("100.cmp1", [], { Dax: "4" })).rejects.toMatchObject({ kind: "said-no" });
    expect(w.problem["said-no"]).toMatch(/said no to being recorded/);
  });

  it("reads the words again and clears the ticks when the wording changed", async () => {
    const api = await previewed();
    for (const label of ["Ash", "Mira", "Dax"]) choose(label, "drop");
    choose("Old Pip", "new");
    fireEvent.click(screen.getByLabelText(c.tick("Old Pip")));
    api.importConsent = async () => ({ version: 8, text: "New words." });
    const real = api.importCheck!;
    api.importCheck = async (...args) => {
      if (args[4]) throw new ApiError("stale");
      return real(...args);
    };
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    expect(await screen.findByText(w.problem.stale!)).toBeTruthy();
    expect(await screen.findByText("New words.")).toBeTruthy();
    expect((screen.getByLabelText(c.tick("Old Pip")) as HTMLInputElement).checked).toBe(false);
  });

  it("two identical consent lines both show, with no key warning", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const api = mockApi("table");
      api.importConsent = async () => ({ version: 7, text: "Same.\nSame.\nOther." });
      await previewed(api);
      expect(screen.getAllByText("Same.")).toHaveLength(2);
      expect(screen.getByText("Other.")).toBeTruthy();
      expect(errors.mock.calls.some((args) => /same key/i.test(String(args[0])))).toBe(false);
    } finally {
      errors.mockRestore();
    }
  });

  it("keeps the old choices, with no consent words, while the switch is off", async () => {
    live.confirm = false;
    const api = await previewed();
    expect(screen.queryByText(c.lead)).toBeNull();
    expect(api.calls.some((x) => x.startsWith("importConsent"))).toBe(false);
    expect(pickFor("Ash").value).toBe("drop");
    expect(within(pickFor("Ash")).queryByText(c.notInDiscord)).toBeNull();
  });

  it("sends the wording's version and only the ticks that are still needed when the import starts", async () => {
    const api = await previewed();
    for (const label of ["Ash", "Mira", "Dax"]) choose(label, "drop");
    choose("Old Pip", "new");
    fireEvent.click(screen.getByLabelText(c.tick("Old Pip")));
    // Cerric is matched to a member who agreed: no tick is sent for them.
    const sent: { confirmed: string[] | undefined; consentVersion: number | undefined }[] = [];
    const start = api.importStart!;
    api.importStart = async (ref, files, plan, links) => {
      sent.push({ confirmed: plan.confirmed, consentVersion: plan.consentVersion });
      return start(ref, files, plan, links);
    };
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(new RegExp(c.keptConfirmed));
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({ confirmed: ["Old Pip"], consentVersion: 7 });
  });

  it("drops a tick when the choice becomes a member who agreed", async () => {
    const api = await previewed();
    for (const label of ["Ash", "Dax"]) choose(label, "drop");
    choose("Old Pip", "new");
    fireEvent.click(screen.getByLabelText(c.tick("Old Pip")));
    choose("Old Pip", "3"); // Mira agreed in Discord
    choose("Mira", "drop");
    fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
    await screen.findByText(/Checked:/);
    expect(api.calls.some((x) => x.includes("|confirmed:"))).toBe(false);
  });

  it("offers a way to try again when the consent words can't be loaded", async () => {
    const api = mockApi("table");
    let tries = 0;
    api.importConsent = async () => {
      if (tries++ === 0) throw new ApiError("network");
      return { version: 7, text: "Words." };
    };
    await previewed(api);
    expect(await screen.findByText(c.loadFailed)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: c.retry }));
    expect(await screen.findByText("Words.")).toBeTruthy();
  });
});

describe("the consent routes over the wire (#3305 PR 1)", () => {
  const over = (status: number, body: unknown, seen: string[] = []) =>
    httpApi("https://api.example.test", (async (url: string) => {
      seen.push(String(url));
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("reads the consent words from /imports/consent", async () => {
    const seen: string[] = [];
    const got = await over(200, { version: 7, text: "A\nB" }, seen).importConsent!("100.cmp1");
    expect(got).toEqual({ version: 7, text: "A\nB" });
    expect(seen[0]).toContain("/me/campaigns/100.cmp1/imports/consent");
  });

  it("reads the API's refusals: stale_consent, blocked and not_ticked", async () => {
    const ask = (detail: string) => over(409, { detail }).importCheck!("100.cmp1", [], {});
    await expect(ask("stale_consent")).rejects.toMatchObject({ kind: "stale" });
    await expect(ask("blocked")).rejects.toMatchObject({ kind: "said-no" });
    await expect(ask("not_ticked")).rejects.toMatchObject({ kind: "not-ticked" });
  });

  it("sends a choice the way PR 1 reads it: objects for ticks, a bare id or drop otherwise", async () => {
    const bodies: Record<string, unknown>[] = [];
    const api = httpApi("https://api.example.test", (async (_u: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ files: [], estimate: { sessions: 0, fastCalls: 0 } }), { status: 200 });
    }) as unknown as typeof fetch);
    const mapping = { Cerric: "1", Ash: "2", "Old Pip": "new", Mira: "drop", Late: "new" };
    await api.importCheck!("100.cmp1", [], mapping, [], { confirmed: ["Ash", "Old Pip"], consentVersion: 7 });
    expect(bodies[0]?.mapping).toEqual({
      Cerric: "1",
      Ash: { confirmed: true, member: "2" },
      "Old Pip": { confirmed: true },
      Mira: "drop",
      Late: { confirmed: false }, // never ticked: the API refuses it as not_ticked
    });
    await api.importStart!("100.cmp1", [], { mapping, confirmed: ["Ash", "Old Pip"], consentVersion: 7, sessions: [] });
    const plan = bodies[1]?.plan as Record<string, unknown>;
    expect(plan.consentVersion).toBe(7);
    expect(plan.confirmed).toBeUndefined();
    expect((plan.mapping as Record<string, unknown>)["Old Pip"]).toEqual({ confirmed: true });
  });
});

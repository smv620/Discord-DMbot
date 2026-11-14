/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "✅ All true" and "🤫 All secret" above what was said at the table (#3645): each answers only the claims shown that are
// still waiting, All secret leaves a state for the game master, a refusal is counted with its plain reason while the rest
// go in, and one "Undo all N" takes the last press back. A player never gets the list, so never the buttons.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi, type Waiting } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { bulkWords as b } from "../src/content/waiting-bulk-words";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(cleanup);

const slow = { timeout: 8000 };
let n = 0;
const calls = (api: AccountApi & { calls: string[] }, start: string) => api.calls.filter((c) => c.startsWith(start));
async function open(ending = "") {
  const api = mockApi("table");
  render(<WebWaiting api={api} campaignRef={`100.bulk${(n += 1)}${ending}`} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("the buttons", () => {
  it("All true answers every claim shown, one at a time, and says how many", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(b.markedTrue(2), { exact: false }, slow)).toBeTruthy();
    expect(calls(api, "answerWaiting").map((c) => c.split(":").slice(2, 5).join(":"))).toEqual(["claim:confirm:c1", "claim:confirm:c2"]);
    expect(calls(api, "answerWaiting").some((c) => c.endsWith(":secret"))).toBe(false);
  });

  it("All secret leaves a state for the game master and says so", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: /All secret/ }));
    expect(await screen.findByText(new RegExp(b.markedSecret(1).replace(".", "\\.")), {}, slow)).toBeTruthy();
    expect(screen.getByText(new RegExp(b.skipped(1).replace(".", "\\.")))).toBeTruthy();
    expect(calls(api, "answerWaiting")).toHaveLength(1);
    expect(calls(api, "answerWaiting")[0]).toContain(":c1:secret");
    expect(await screen.findByText("Roan is dead")).toBeTruthy(); // (still waiting)
  });

  it("counts a refused one with its reason, and the rest still go in", async () => {
    const api = await open(".bulk-refuse");
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(new RegExp(b.markedTrue(1).replace(".", "\\.")), {}, slow)).toBeTruthy();
    expect(screen.getByText(new RegExp("could not be saved"))).toBeTruthy();
    expect(calls(api, "answerWaiting")).toHaveLength(2);
  });

  it("shows n of N while it runs", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    expect(await screen.findByText(b.stage)).toBeTruthy();
  });
});

describe("while a press is on", () => {
  it("a claim's own buttons wait, and everything works again after the re-read fails", async () => {
    const api = mockApi("table");
    const read = api.webWaiting!.bind(api);
    let fail = false;
    api.webWaiting = async (ref) => {
      if (fail) throw new ApiError("network");
      return read(ref);
    };
    render(<WebWaiting api={api} campaignRef="100.bulkz" />);
    await screen.findByRole("heading", { name: w.heading });
    fail = true; // (the re-read after the press fails)
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: w.confirm }).every((x) => (x as HTMLButtonElement).disabled)).toBe(true));
    await screen.findByText(b.markedTrue(2), { exact: false }, slow);
    await waitFor(() => expect(screen.queryByText(b.stage)).toBeNull(), slow);
    fail = false;
    expect(screen.getByRole("button", { name: /Undo all 2/ })).toBeTruthy(); // (the buttons are live again)
  }, 20000);
});

describe("Undo all after a failed re-read", () => {
  it("the buttons work again", async () => {
    const api = mockApi("table");
    const read = api.webWaiting!.bind(api);
    let fail = false;
    api.webWaiting = async (ref) => {
      if (fail) throw new ApiError("network");
      return read(ref);
    };
    render(<WebWaiting api={api} campaignRef="100.bulky" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    await screen.findByText(b.markedTrue(2), { exact: false }, slow);
    await waitFor(() => expect(screen.queryByText(b.stage)).toBeNull(), slow);
    fail = true;
    fireEvent.click(await screen.findByRole("button", { name: /Undo all 2/ }, slow));
    await screen.findByText(b.undid(2), { exact: false }, slow);
    await waitFor(() => expect(screen.queryByText(b.stage)).toBeNull(), slow);
    fail = false;
    expect(screen.queryByRole("button", { name: /Undo all/ })).toBeNull(); // (done, and nothing is stuck)
  }, 30000);
});

describe("Undo all", () => {
  it("takes the last press back, answer by answer, and the claims wait again", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    await screen.findByText(b.markedTrue(2), { exact: false }, slow);
    await waitFor(() => expect(screen.queryByText(b.stage)).toBeNull(), slow); // (the press is over)
    fireEvent.click(await screen.findByRole("button", { name: /Undo all 2/ }, slow));
    expect(await screen.findByText(b.undid(2), { exact: false }, slow)).toBeTruthy();
    expect(calls(api, "answerWaiting").filter((c) => c.includes(":claim:undo:"))).toHaveLength(2);
    expect(await screen.findByText("Mira Voss knows Far Hall")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^↩️ Undo all/ })).toBeNull();
  }, 20000);

  it("is not offered when the answers say no change (an API without it)", async () => {
    const api = mockApi("table");
    const real = api.answerWaiting!.bind(api);
    api.answerWaiting = async (...args) => {
      await real(...args);
      return {};
    };
    render(<WebWaiting api={api} campaignRef="100.bulkx" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getByRole("button", { name: /All true/ }));
    await screen.findByText(b.markedTrue(2), { exact: false }, slow);
    expect(screen.queryByRole("button", { name: /Undo all/ })).toBeNull();
  });
});

describe("who sees it", () => {
  it("a player (or another DM) gets no list, so no buttons", async () => {
    const api = mockApi("table");
    render(<WebWaiting api={api} campaignRef="100.cmp1.web-player" />);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("webWaiting:"))).toBe(true));
    expect(screen.queryByRole("button", { name: /All true/ })).toBeNull();
  });

  it("nothing waiting, no buttons", async () => {
    const api = mockApi("table");
    api.webWaiting = async (): Promise<Waiting> => ({ claims: [], backstory: [], which: [], counts: { claims: 0, backstory: 0, ready: 0, which: 0, all: 0 } });
    render(<WebWaiting api={api} campaignRef="100.cmp1" />);
    await screen.findByText(w.none);
    expect(screen.queryByRole("button", { name: /All true/ })).toBeNull();
  });
});

describe("the secret buttons are purple, readable in both themes", () => {
  it("All secret and a claim's own secret button carry the secret style", async () => {
    await open();
    expect(screen.getByRole("button", { name: /All secret/ }).classList.contains("secret")).toBe(true);
    expect(screen.getByRole("button", { name: w.confirmSecret }).classList.contains("secret")).toBe(true);
    expect(screen.getByRole("button", { name: /All true/ }).classList.contains("secret")).toBe(false);
  });

  it("every colour pair is at least 4.5:1 (AA), in the light and the dark theme", () => {
    const css = readFileSync("src/styles/account.css", "utf8");
    const block = (start: string) => css.slice(css.indexOf(start), css.indexOf("}", css.indexOf(start)));
    const grab = (text: string) => Object.fromEntries([...text.matchAll(/--secret-([a-z-]+): (#[0-9a-f]{6});/g)].map((m) => [m[1], m[2]]));
    const light = grab(block(".account {\n  --secret-fill"));
    const dark = grab(block(':root[data-theme="dark"] .account {'));
    const lum = (hex: string): number => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    const ratio = (a: string, b: string): number => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
      return (hi! + 0.05) / (lo! + 0.05);
    };
    const pages = { light: "#ffffff", dark: "#18211e" } as const;
    for (const [name, t] of [["light", light], ["dark", dark]] as const) {
      expect(ratio(t["text"]!, t["fill"]!), `${name} text on fill`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["text-hover"]!, t["fill-hover"]!), `${name} hover`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["edge"]!, pages[name]), `${name} edge on page`).toBeGreaterThanOrEqual(3); // (a border: 3:1)
    }
  });
});

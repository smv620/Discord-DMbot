/** @jsxImportSource preact */
// @vitest-environment happy-dom
// What waits for the DM on the Story graph (#2635, #2652): facts heard at the table, connections from a backstory
// (grouped by upload, with Keep all) and "Which Mara?" questions. Shown only while WEB_WAITING_LIVE is true
// (false today), so these tests switch it on. The DM decides each one; the page sends the answer and reads the
// list again. A player never gets the list.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type AccountApi, type ApiErrorKind } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/web-waiting", async (original) => {
  const real = await original<typeof import("../src/content/web-waiting")>();
  return {
    ...real,
    get WEB_WAITING_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const REF = "100.cmp1";
const press = (name: string | RegExp, within_?: HTMLElement) =>
  fireEvent.click((within_ ? within(within_) : screen).getByRole("button", { name }));

/** The buttons wait while an answer is sent and the list is read again; the next press comes after. */
const settled = () => waitFor(() => expect(screen.queryByText(w.savingStage)).toBeNull());

async function open(api = mockApi("table")) {
  render(<WebWaiting api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("on the Story graph", () => {
  it("is not there while it is off", async () => {
    live.on = false;
    render(<Web api={mockApi("table")} path={`/account/web/${REF}`} />);
    await screen.findByText(/names?, \d+ lines?/);
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("is there for a DM when it is on, under the map's count line", async () => {
    live.on = true;
    const api = mockApi("table");
    render(<Web api={api} path={`/account/web/${REF}`} />);
    expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("webWaiting:"))).toBe(true);
  });
});

describe("the list", () => {
  it("counts everything that waits and lists it in three parts", async () => {
    await open();
    expect(screen.getByText(w.count(7))).toBeTruthy(); // 2 facts, 4 connections, 1 question
    expect(screen.getByRole("heading", { name: w.claimsHeading })).toBeTruthy();
    expect(screen.getByRole("heading", { name: w.backstoryHeading })).toBeTruthy();
    expect(screen.getByRole("heading", { name: w.whichHeading })).toBeTruthy();
    expect(screen.getByText("Mira Voss knows Far Hall")).toBeTruthy();
  });

  it("an API that can't say, or a player, leaves nothing on the page", async () => {
    const old = mockApi("table");
    delete (old as AccountApi).webWaiting;
    render(<WebWaiting api={old} campaignRef={REF} />);
    await Promise.resolve();
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
    cleanup();
    const api = mockApi("table");
    api.webWaiting = async () => {
      throw new ApiError("not-found");
    };
    render(<WebWaiting api={api} campaignRef={REF} />);
    await waitFor(() => expect(screen.queryByRole("heading", { name: w.heading })).toBeNull());
  });

  it("a failed read says so and can look again", async () => {
    const api = mockApi("table");
    let first = true;
    const real = api.webWaiting!.bind(api);
    api.webWaiting = async (ref) => {
      if (first) {
        first = false;
        throw new ApiError("server");
      }
      return real(ref);
    };
    render(<WebWaiting api={api} campaignRef={REF} />);
    expect((await screen.findByRole("alert")).textContent).toBe(w.loadFailed);
    press(w.reload);
    expect(await screen.findByText(w.count(7))).toBeTruthy();
  });
});

describe("facts said at the table", () => {
  it("Yes, that's true sends the confirm and the list reads again", async () => {
    const api = await open();
    const card = screen.getByText("Mira Voss knows Far Hall").closest(".panel") as HTMLElement;
    press(w.confirm, card);
    expect(await screen.findByText(w.done.confirm)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:claim:confirm:c1`);
    await waitFor(() => expect(screen.queryByText("Mira Voss knows Far Hall")).toBeNull());
    expect(screen.getByText(w.count(6))).toBeTruthy();
  });

  it("True, but keep it secret sends the secret flag, and is not offered for a condition", async () => {
    const api = await open();
    const roan = screen.getByText("Roan is dead").closest(".panel") as HTMLElement;
    expect(within(roan).queryByRole("button", { name: w.confirmSecret })).toBeNull();
    const mira = screen.getByText("Mira Voss knows Far Hall").closest(".panel") as HTMLElement;
    press(w.confirmSecret, mira);
    expect(await screen.findByText(w.done.confirmSecret)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:claim:confirm:c1:secret`);
  });

  it("Not right drops it", async () => {
    const api = await open();
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.done.reject)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:claim:reject:c2`);
  });
});

describe("connections from a backstory", () => {
  it("are grouped by upload, and an old one with no label says so", async () => {
    await open();
    expect(screen.getByRole("heading", { name: w.fromUpload("Lore, 24 Oct") })).toBeTruthy();
    expect(screen.getByRole("heading", { name: w.fromEarlier })).toBeTruthy();
  });

  it("Keep all from an upload sends only the ready ones it shows, and a name that isn't confirmed waits", async () => {
    const api = await open();
    press(w.keepAll(2));
    expect(await screen.findByText(w.kept(2))).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:backstory:keep_all:b1,b2`);
    // the one waiting on a name is still there, with the reason, and no Keep for it
    const waiting = screen.getByText("Rook knows Newcomer").closest(".panel") as HTMLElement;
    expect(within(waiting).getByText(w.waitingOn(["Newcomer"]))).toBeTruthy();
    expect(within(waiting).queryByRole("button", { name: w.keep })).toBeNull();
    expect(within(waiting).getByRole("button", { name: w.skip })).toBeTruthy();
  });

  it("never sends more than 50 in one Keep all, and says how many are left", async () => {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    api.webWaiting = async (ref) => {
      const got = await real(ref);
      const many = Array.from({ length: 60 }, (_, n) => ({
        id: `m${n}`, subject: "Rook", object: `Place ${n}`, how: "knows", secret: false, upload: "Big upload", ready: true, waitingOn: [],
      }));
      return { ...got, backstory: many, counts: { ...got.counts, backstory: 60, ready: 60 } };
    };
    api.answerWaiting = async (_ref, answer) => {
      api.calls.push(`answered:${"ids" in answer ? answer.ids.length : 0}`);
      return { kept: "ids" in answer ? answer.ids.length : 0 };
    };
    await open(api);
    press(w.keepAll(50));
    expect(await screen.findByText(w.keepAllCut(50, 10))).toBeTruthy();
    expect(api.calls).toContain("answered:50");
  });

  it("Keep as secret, Keep and Skip each send their own answer", async () => {
    const api = await open();
    press(w.keepSecret, screen.getByText("Rook knows Far Hall").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.done.keepSecret)).toBeTruthy();
    await settled();
    expect(api.calls).toContain(`answerWaiting:${REF}:backstory:keep:b1:secret`);
    press(w.keep, screen.getByText("Mira Voss grew up in Harrow's Keep").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.done.keep)).toBeTruthy();
    await settled();
    expect(api.calls).toContain(`answerWaiting:${REF}:backstory:keep:b4`);
    press(w.skip, screen.getByText("Rook knows Newcomer").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.done.skip)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:backstory:skip:b3`);
  });

  it("a secret connection says players won't see it", async () => {
    await open();
    const secret = screen.getByText("Rook grew up with Mira Voss").closest(".panel") as HTMLElement;
    expect(within(secret).getByText(w.secretNote)).toBeTruthy();
    expect(within(secret).queryByRole("button", { name: w.keepSecret })).toBeNull();
    expect(within(secret).getByRole("button", { name: w.keepIsSecret })).toBeTruthy(); // (as on Discord)
  });
});

describe("Which Mara?", () => {
  it("asks the question with a button for each candidate and Neither; a hidden one is marked", async () => {
    await open();
    const card = screen.getByText(w.whichQuestion("Mara")).closest(".panel") as HTMLElement;
    expect(within(card).getByText(w.whichLine("Rook", "knows", "Mara", true))).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Mara Ashgrove" })).toBeTruthy();
    expect(within(card).getByRole("button", { name: `${w.hiddenWord} Mara Vell` })).toBeTruthy();
    expect(within(card).getByText(w.hiddenNote)).toBeTruthy();
    expect(within(card).getByRole("button", { name: w.neither })).toBeTruthy();
  });

  it("a pick sends the candidate's id", async () => {
    const api = await open();
    press("Mara Ashgrove");
    expect(await screen.findByText(w.done.pick)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:which:pick:w1:e1`);
    await waitFor(() => expect(screen.queryByText(w.whichQuestion("Mara"))).toBeNull());
  });

  it("Neither drops the question", async () => {
    const api = await open();
    press(w.neither);
    expect(await screen.findByText(w.done.neither)).toBeTruthy();
    expect(api.calls).toContain(`answerWaiting:${REF}:which:neither:w1`);
  });
});

describe("after an answer", () => {
  it("keeps the buttons waiting until the list has been read again, so nobody answers what is gone", async () => {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    let release: () => void = () => {};
    let held = false;
    api.webWaiting = async (ref) => {
      if (held) await new Promise<void>((resolve) => (release = resolve));
      return real(ref);
    };
    await open(api);
    held = true; // the re-read after the answer is slow
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    await screen.findByText(w.done.reject);
    for (const b of screen.getAllByRole("button", { name: w.keep })) expect((b as HTMLButtonElement).disabled).toBe(true);
    held = false;
    release();
    await waitFor(() => expect((screen.getAllByRole("button", { name: w.keep })[0] as HTMLButtonElement).disabled).toBe(false));
  });

  it("puts focus on the heading, since the answered item has gone", async () => {
    await open();
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: w.heading })));
  });

  it("two quick taps send one answer", async () => {
    const api = await open();
    const card = screen.getByText("Roan is dead").closest(".panel") as HTMLElement;
    const reject = within(card).getByRole("button", { name: w.reject });
    fireEvent.click(reject);
    fireEvent.click(reject);
    await screen.findByText(w.done.reject);
    expect(api.calls.filter((c) => c.startsWith("answerWaiting:")).length).toBe(1);
  });

  it("a Keep all that says nothing of its count does not claim one", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => ({});
    await open(api);
    press(w.keepAll(2));
    expect(await screen.findByText(w.kept(undefined))).toBeTruthy();
    expect(screen.queryByText(/Kept \d+/)).toBeNull();
  });

  it("a pick that is not one of the candidates is refused as gone", async () => {
    const api = mockApi("table");
    await expect(api.answerWaiting!(REF, { kind: "which", action: "pick", id: "w1", entity: "nope" })).rejects.toMatchObject({ kind: "gone" });
  });

  it("one campaign's list is never drawn under another", async () => {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    let release: () => void = () => {};
    api.webWaiting = async (ref) => {
      if (ref === REF) await new Promise<void>((resolve) => (release = resolve)); // slow
      return real(ref);
    };
    const view = render(<WebWaiting key={REF} api={api} campaignRef={REF} />);
    view.rerender(<WebWaiting key="100.cmp2" api={api} campaignRef="100.cmp2" />);
    release();
    await Promise.resolve();
    expect(api.calls.filter((c) => c === `webWaiting:${REF}`).length).toBe(1);
  });
});

describe("skipping a connection that waits on a name", () => {
  it("dismisses it: skipping writes no fact", async () => {
    const api = await open();
    press(w.skip, screen.getByText("Rook knows Newcomer").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.done.skip)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Rook knows Newcomer")).toBeNull());
    expect(api.calls).toContain(`answerWaiting:${REF}:backstory:skip:b3`);
  });

  it("but Keep on it is refused as gone, as core does, and the card stays", async () => {
    const api = mockApi("table");
    await expect(api.answerWaiting!(REF, { kind: "backstory", action: "keep", id: "b3" })).rejects.toMatchObject({ kind: "gone" });
    await expect(api.webWaiting!(REF).then((l) => l.backstory.some((b) => b.id === "b3"))).resolves.toBe(true);
  });
});

describe("what the card does after a refusal", () => {
  /** A pretend API whose first answer is refused and whose list afterwards is as given. */
  function refusing(kind: ApiErrorKind, dropsIt: boolean) {
    const api = mockApi("table");
    const real = api.webWaiting!.bind(api);
    let refused = false;
    api.answerWaiting = async () => {
      refused = true;
      throw new ApiError(kind);
    };
    api.webWaiting = async (ref) => {
      const got = await real(ref);
      if (refused && dropsIt) return { ...got, claims: got.claims.filter((c) => c.id !== "c2"), counts: { ...got.counts, claims: 1, all: got.counts.all - 1 } };
      return got;
    };
    return api;
  }
  const card = () => screen.queryByText("Roan is dead");

  it.each(["gone", "stopped-recording"] as const)("%s is final: the card goes away", async (kind) => {
    await open(refusing(kind, true));
    press(w.reject, card()!.closest(".panel") as HTMLElement);
    await screen.findByText(w.errors[kind]!);
    await waitFor(() => expect(card()).toBeNull());
  });

  it.each(["name-first", "cannot-write"] as const)("%s keeps the card, with its buttons working again", async (kind) => {
    await open(refusing(kind, false));
    press(w.reject, card()!.closest(".panel") as HTMLElement);
    await screen.findByText(w.errors[kind]!);
    await settled();
    expect(card()).toBeTruthy();
    for (const b of screen.getAllByRole("button", { name: w.reject })) expect((b as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("when DMbot refuses", () => {
  it.each([
    ["gone", "gone"],
    ["name-first", "name-first"],
    ["stopped-recording", "stopped-recording"],
    ["cannot-write", "cannot-write"],
    ["queue-full", "queue-full"],
    ["still-waiting", "still-waiting"],
    ["network", "network"],
  ] as const)("%s is said in plain words and the list is read again", async (kind, key) => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw new ApiError(kind);
    };
    await open(api);
    const reads = api.calls.filter((c) => c.startsWith("webWaiting:")).length;
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.errors[key]!)).toBeTruthy();
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("webWaiting:")).length).toBe(reads + 1));
  });

  it("a code the page doesn't know gets the general sentence, never the code", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw new ApiError("something-new" as never);
    };
    await open(api);
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.errors.other!)).toBeTruthy();
  });

  it("shows that DMbot is working while an answer is sent, and the buttons wait", async () => {
    const api = mockApi("table");
    let release: () => void = () => {};
    api.answerWaiting = () => new Promise((resolve) => (release = () => resolve({})));
    await open(api);
    press(w.reject, screen.getByText("Roan is dead").closest(".panel") as HTMLElement);
    expect(await screen.findByText(w.savingStage)).toBeTruthy();
    for (const b of screen.getAllByRole("button", { name: w.reject })) expect((b as HTMLButtonElement).disabled).toBe(true);
    release();
    await waitFor(() => expect(screen.queryByText(w.savingStage)).toBeNull());
  });
});

describe("over the wire", () => {
  const reply = (status: number, body: unknown, seen: string[] = []) =>
    httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
      seen.push(`${init.method ?? "GET"} ${url} ${String(init.body ?? "")}`);
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("reads the list from the waiting route", async () => {
    const seen: string[] = [];
    await reply(200, { claims: [], backstory: [], which: [], counts: { all: 0 } }, seen).webWaiting!("1.a");
    expect(seen).toEqual(["GET https://api.example.test/me/campaigns/1.a/web/waiting "]);
  });

  it("sends a which pick, a keep all and a claim in the shapes core takes, and polls the answer", async () => {
    const seen: string[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      seen.push(`${init.method ?? "GET"} ${String(url).replace("https://api.example.test", "")} ${String(init.body ?? "")}`);
      if (init.method === "POST") return new Response(JSON.stringify({ answer: "x1" }), { status: 202 });
      return new Response(JSON.stringify({ state: "done", kept: 2 }), { status: 200 });
    }) as unknown as typeof fetch;
    const api = httpApi("https://api.example.test", fetcher);
    await api.answerWaiting!("1.a", { kind: "which", action: "pick", id: "w1", entity: "e1" });
    const kept = await api.answerWaiting!("1.a", { kind: "backstory", action: "keep_all", ids: ["b1", "b2"] });
    await api.answerWaiting!("1.a", { kind: "claim", action: "confirm", id: "c1", secret: true });
    expect(kept).toEqual({ kept: 2 });
    expect(seen).toContain('POST /me/campaigns/1.a/web/waiting {"kind":"which","action":"pick","id":"w1","entity":"e1"}');
    expect(seen).toContain('POST /me/campaigns/1.a/web/waiting {"kind":"backstory","action":"keep_all","ids":["b1","b2"]}');
    expect(seen).toContain('POST /me/campaigns/1.a/web/waiting {"kind":"claim","action":"confirm","id":"c1","secret":true}');
    expect(seen.filter((s) => s.startsWith("GET /me/campaigns/1.a/web/answers/x1")).length).toBe(3);
  });

  it.each([
    ["gone", "gone"],
    ["name_first", "name-first"],
    ["stopped_recording", "stopped-recording"],
    ["cannot_write", "cannot-write"],
    ["queue_full", "queue-full"],
  ])("a refused answer (%s) becomes the page's own kind", async (code, kind) => {
    const fetcher = (async (_url: string, init: RequestInit) => {
      if (init.method === "POST") return new Response(JSON.stringify({ answer: "x1" }), { status: 202 });
      return new Response(JSON.stringify({ state: "failed", error: code }), { status: 200 });
    }) as unknown as typeof fetch;
    const error = await httpApi("https://api.example.test", fetcher)
      .answerWaiting!("1.a", { kind: "claim", action: "reject", id: "c1" })
      .catch((e: unknown) => e);
    expect((error as ApiError).kind).toBe(kind);
  });
});

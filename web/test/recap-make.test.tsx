/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Recaps for past sessions (#2610; core #2608, #2609), against CloudDev's approved routes: the list
// (`GET past-sessions`), `POST /me/recaps/{ref}/again` and `POST /me/campaigns/{ref}/recaps/upload`, with
// the switch on. Make, make again with the one question, add one someone wrote (the right-to-use box only
// when the API asks), the shared wait panel, every code in plain words, and nothing at all for a player.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type ApiErrorKind } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapMake from "../src/account/RecapMake";
import { makeWords as w } from "../src/content/recap-make";

vi.mock("../src/content/recap-make", async (original) => ({
  ...(await original<typeof import("../src/content/recap-make")>()),
  RECAP_MAKE_LIVE: true,
}));

const present = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/present-words", async (original) => ({
  ...(await original<typeof import("../src/content/present-words")>()),
  get PRESENT_PER_SCENE_LIVE() {
    return present.on;
  },
}));

/** A label as an exact pattern (the words hold brackets). */
const rx = (label: string): RegExp => new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  cleanup();
  present.on = false;
  vi.useRealTimers();
});

async function open(campaign = "100.cmp1", api = mockApi("table")) {
  render(<RecapMake api={api} campaignRef={campaign} />);
  await screen.findByRole("heading", { name: w.heading });
  return api;
}

describe("the list of past sessions", () => {
  it("offers Make the recap where there is none, Make it again where there is one, and neither without a transcript", async () => {
    await open();
    expect(screen.getAllByRole("button", { name: w.make })).toHaveLength(1); // session 3: no recap, has lines
    expect(screen.getAllByRole("button", { name: w.again })).toHaveLength(2); // sessions 4 and 2
    expect(screen.getAllByRole("button", { name: w.add })).toHaveLength(4);
    expect(screen.getByText(w.noTranscript)).toBeTruthy(); // session 1: nothing kept
  });

  it("shows nothing to a player (the API says not allowed)", async () => {
    const api = mockApi("table");
    const { container } = render(<RecapMake api={api} campaignRef="100.player" />);
    await waitFor(() => expect(api.calls).toContain("pastSessions:100.player"));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("making a recap", () => {
  it("asks for the session's own ref and shows the wait panel until it is ready", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.make }));
    expect(await screen.findByText(w.stage["queued"] as string)).toBeTruthy();
    expect(api.calls).toContain("makeRecap:100.cmp1.1790000000");
    await vi.advanceTimersByTimeAsync(9_000);
    await waitFor(() => expect(screen.queryByText(w.stage["queued"] as string)).toBeNull());
  });

  it("asks one question before replacing an approved recap, says it stays until the new one is approved, and keeps it on No", async () => {
    const api = await open();
    fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!); // the newest session: approved
    expect(await screen.findByText(w.askAgain(true))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(screen.queryByText(w.askAgain(true))).toBeNull();
    expect(api.calls.filter((c) => c.startsWith("makeRecap"))).toEqual(["makeRecap:100.cmp1.1791000000"]);
  });

  describe("when the DM saved who was in a scene (#2684)", () => {
    const withBeats = async (beats: { n: number; present: string[]; saved?: boolean }[] | "unreadable") => {
      present.on = true;
      const api = mockApi("table");
      const base = await api.recap!("200000000000000001.cmp1.1791000000");
      api.recap = async () => {
        if (beats === "unreadable") throw new ApiError("server");
        return { ...base, beats };
      };
      await open("100.cmp1", api);
      fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
      await screen.findByText(w.askAgain(true));
      await waitFor(() => expect(screen.queryByText(w.checkingLists)).toBeNull());
    };

    it("says the lists will be cleared", async () => {
      await withBeats([{ n: 1, present: ["Mira"], saved: true }, { n: 2, present: [] }]);
      expect(screen.getByText(w.askAgainLists)).toBeTruthy();
    });

    it("says nothing about lists when none was saved", async () => {
      await withBeats([{ n: 1, present: ["Mira"] }]);
      expect(screen.queryByText(w.askAgainLists)).toBeNull();
      expect(screen.queryByText(w.askAgainMaybeLists)).toBeNull();
    });

    it("warns anyway when the recap could not be read to check", async () => {
      await withBeats("unreadable");
      expect(screen.getByText(w.askAgainMaybeLists)).toBeTruthy();
      expect(screen.queryByText(w.askAgainLists)).toBeNull();
    });

    it("looks before it asks, and holds the buttons meanwhile", async () => {
      present.on = true;
      const api = mockApi("table");
      const base = await api.recap!("200000000000000001.cmp1.1791000000");
      let release: (r: typeof base) => void = () => {};
      api.recap = () => new Promise((resolve) => (release = resolve));
      await open("100.cmp1", api);
      fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
      await waitFor(() => expect((screen.getAllByRole("button", { name: w.again })[0] as HTMLButtonElement).disabled).toBe(true));
      expect(api.calls.some((c) => c.startsWith("makeRecap"))).toBe(false);
      release({ ...base, beats: [{ n: 1, present: ["Mira"], saved: true }] });
      expect(await screen.findByText(w.askAgainLists)).toBeTruthy();
      expect(screen.getByText(w.askAgain(true))).toBeTruthy();
      expect((screen.getByRole("button", { name: w.yesAgain }) as HTMLButtonElement).disabled).toBe(false);
    });

    it("says it too when an uploaded recap would replace the recap", async () => {
      present.on = true;
      const api = mockApi("table");
      const base = await api.recap!("200000000000000001.cmp1.1791000000");
      api.recap = async () => ({ ...base, beats: [{ n: 1, present: ["Mira"], saved: true }] });
      await open("100.cmp1", api);
      fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!);
      await screen.findByRole("heading", { name: w.form.heading });
      fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
      fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
      fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
      expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
      expect(await screen.findByText(w.askAgainLists)).toBeTruthy();
    });

    describe("which question each kind of recap gets (#2764)", () => {
      /** The pretend list with the newest session's recap set to what the case needs. */
      const withRecap = async (recap: Record<string, unknown>) => {
        present.on = true;
        const api = mockApi("table");
        const base = await api.recap!("200000000000000001.cmp1.1791000000");
        api.recap = async () => ({ ...base, beats: [{ n: 1, present: ["Mira"], saved: true }] });
        const list = api.pastSessions!;
        api.pastSessions = async (ref) => {
          const got = await list(ref);
          const first = got.sessions[0]!;
          return { sessions: [{ ...first, recap: { ...first.recap!, ...recap } }, ...got.sessions.slice(1)] } as typeof got;
        };
        await open("100.cmp1", api);
        return api;
      };
      const upload = async () => {
        fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!);
        await screen.findByRole("heading", { name: w.form.heading });
        fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
        fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
        fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
      };

      it("a finished draft nobody approved: the upload says it is replaced", async () => {
        await withRecap({ approved: false, status: "draft" });
        await upload();
        expect(await screen.findByText(w.askReplaceDraft)).toBeTruthy();
        expect(screen.queryByText(w.askReplaceExisting)).toBeNull();
      });

      it("an approved recap: the upload says the old one is kept", async () => {
        await withRecap({ approved: true });
        await upload();
        expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
      });

      it("a posted recap: the upload says the old one is kept", async () => {
        await withRecap({ approved: false, status: "posted" });
        await upload();
        expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
      });

      it("making it again promises the old one only for an approved or a posted recap", async () => {
        await withRecap({ approved: false, status: "draft" });
        fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
        expect(await screen.findByText(w.askAgain(false))).toBeTruthy();
        cleanup();
        await withRecap({ approved: false, status: "posted" });
        fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
        expect(await screen.findByText(w.askAgain(true))).toBeTruthy();
      });

      it("a finished recap waiting for its check is not called unfinished", async () => {
        await withRecap({ state: "waiting_for_check", approved: false, status: "draft" });
        fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
        expect(await screen.findByText(w.askAgain(false))).toBeTruthy();
        expect(screen.queryByText(w.askAgainUnfinished)).toBeNull();
      });

      it("only a failed recap gets the unfinished words", async () => {
        await withRecap({ state: "failed", approved: false });
        fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
        expect(await screen.findByText(w.askAgainUnfinished)).toBeTruthy();
      });

      it("a yes does not outlive its form: yes, then the rights box, then Cancel, then Add again asks again", async () => {
        const api = await withRecap({ approved: true });
        await upload();
        fireEvent.click(await screen.findByRole("button", { name: w.yesReplace }));
        await screen.findByLabelText(rx(w.form.right)); // the form is back with the rights box
        fireEvent.click(screen.getByRole("button", { name: w.form.cancel }));
        fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!);
        await screen.findByRole("heading", { name: w.form.heading });
        fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
        fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
        fireEvent.click(screen.getByLabelText(rx(w.form.right))); // the rights box is still asked for
        fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
        expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
        expect(screen.getByText(w.askAgainLists)).toBeTruthy();
        // Only the one send after the first yes: the second Add went to the question, not to the API.
        expect(api.calls.filter((c) => c.startsWith("addRecap"))).toHaveLength(1);
      });
    });

    describe("a recap that failed (#2764)", () => {
      const failedSession = async (beats: { n: number; present: string[]; saved?: boolean }[] | "unreadable") => {
        present.on = true;
        const api = mockApi("table");
        const base = await api.recap!("200000000000000001.cmp1.1791000000");
        api.recap = async () => {
          if (beats === "unreadable") throw new ApiError("server");
          return { ...base, beats };
        };
        await open("100.cmp1", api);
        return api;
      };
      const failedAgain = () => screen.getAllByRole("button", { name: w.again })[1]!; // session 2: failed

      it("asks before making it again when scenes have saved lists, and promises no old recap", async () => {
        const api = await failedSession([{ n: 1, present: ["Mira"], saved: true }]);
        fireEvent.click(failedAgain());
        expect(await screen.findByText(w.askAgainUnfinished)).toBeTruthy();
        expect(screen.getByText(w.askAgainLists)).toBeTruthy();
        expect(screen.queryByText(w.askAgain(true))).toBeNull();
        expect(screen.queryByText(w.askAgain(false))).toBeNull();
        expect(api.calls.some((c) => c.startsWith("makeRecap"))).toBe(false);
      });

      it("changes nothing on No", async () => {
        const api = await failedSession([{ n: 1, present: ["Mira"], saved: true }]);
        fireEvent.click(failedAgain());
        fireEvent.click(await screen.findByRole("button", { name: w.keep }));
        expect(screen.queryByText(w.askAgainLists)).toBeNull();
        expect(api.calls.some((c) => c.startsWith("makeRecap"))).toBe(false);
      });

      it("makes it, once, on Yes", async () => {
        const api = await failedSession([{ n: 1, present: ["Mira"], saved: true }]);
        fireEvent.click(failedAgain());
        fireEvent.click(await screen.findByRole("button", { name: w.yesAgain }));
        await waitFor(() => expect(api.calls.filter((c) => c.startsWith("makeRecap"))).toEqual(["makeRecap:100.cmp1.1789000000:replace"]));
      });

      it("starts at once when no list was saved", async () => {
        const api = await failedSession([{ n: 1, present: ["Mira"] }]);
        fireEvent.click(failedAgain());
        await waitFor(() => expect(api.calls).toContain("makeRecap:100.cmp1.1789000000"));
        expect(screen.queryByText(w.askAgainUnfinished)).toBeNull();
      });

      it("warns anyway when the recap could not be read", async () => {
        await failedSession("unreadable");
        fireEvent.click(failedAgain());
        expect(await screen.findByText(w.askAgainMaybeLists)).toBeTruthy();
      });

      it("asks before an upload replaces it", async () => {
        const api = await failedSession([{ n: 1, present: ["Mira"], saved: true }]);
        fireEvent.click(screen.getAllByRole("button", { name: w.add })[2]!);
        await screen.findByRole("heading", { name: w.form.heading });
        fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
        fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
        fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
        expect(await screen.findByText(w.askReplaceUnfinished)).toBeTruthy();
        expect(screen.getByText(w.askAgainLists)).toBeTruthy();
        expect(api.calls.some((c) => c.startsWith("addRecap"))).toBe(false);
      });
    });

    it("asks once, then the rights box, then sends with the yes kept (#2764)", async () => {
      present.on = true;
      const api = mockApi("table");
      const base = await api.recap!("200000000000000001.cmp1.1791000000");
      api.recap = async () => ({ ...base, beats: [{ n: 1, present: ["Mira"], saved: true }] });
      await open("100.cmp1", api);
      fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!);
      await screen.findByRole("heading", { name: w.form.heading });
      fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
      fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
      fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
      fireEvent.click(await screen.findByRole("button", { name: w.yesReplace }));
      fireEvent.click(await screen.findByLabelText(rx(w.form.right)));
      fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
      await waitFor(() => expect(api.calls.filter((c) => c.startsWith("addRecap"))).toEqual([expect.stringMatching(/:replace$/), expect.stringMatching(/:replace:rights$/)]));
      expect(screen.queryByText(w.askReplaceExisting)).toBeNull();
    });

    it("does not look while the feature is off", async () => {
      present.on = false;
      const api = await open();
      fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
      await screen.findByText(w.askAgain(true));
      expect(api.calls.some((c) => c.startsWith("recap:"))).toBe(false);
      expect(screen.queryByText(w.askAgainLists)).toBeNull();
    });
  });

  it("sends replace after Yes", async () => {
    const api = await open();
    fireEvent.click(screen.getAllByRole("button", { name: w.again })[0]!);
    fireEvent.click(await screen.findByRole("button", { name: w.yesAgain }));
    await waitFor(() => expect(api.calls).toContain("makeRecap:100.cmp1.1791000000:replace"));
  });

  it("a recap that is busy is looked at again, not shown as an error", async () => {
    const api = mockApi("table");
    api.makeRecap = async () => {
      throw new ApiError("busy");
    };
    await open("100.cmp1", api);
    fireEvent.click(screen.getByRole("button", { name: w.make }));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("pastSessions")).length).toBe(2));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says in plain words when the transcript is gone", async () => {
    const api = mockApi("table");
    api.makeRecap = async () => {
      throw new ApiError("no-lines");
    };
    await open("100.cmp1", api);
    fireEvent.click(screen.getByRole("button", { name: w.make }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["no-lines"]);
  });
});

describe("adding a recap someone wrote", () => {
  async function fill(index = 2, link?: string) {
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[index]!);
    const form = await screen.findByRole("heading", { name: w.form.heading });
    expect(document.activeElement).toBe(form);
    if (link === undefined) {
      fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "The party crossed the river." } });
    } else {
      fireEvent.click(screen.getByLabelText(w.form.link));
      fireEvent.input(screen.getByLabelText(rx(w.form.link_)), { target: { value: link } });
    }
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
  }

  it("shows no right-to-use box until the API asks for it, then sends it with the recap", async () => {
    const api = await open();
    await fill(1); // session 3: no recap yet
    expect(screen.queryByLabelText(rx(w.form.right))).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["needs-rights"]);
    expect(api.calls).toContain("addRecap:100.cmp1:1790000000:text");
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect(await screen.findByText(w.stage["queued"] as string)).toBeTruthy();
    expect(api.calls).toContain("addRecap:100.cmp1:1790000000:text:rights");
  });

  it("does not need the box again once the API has been told", async () => {
    const api = await open();
    await fill();
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await screen.findByText(w.stage["queued"] as string);
    cleanup();
    render(<RecapMake api={api} campaignRef="100.cmp1" />);
    await screen.findByRole("heading", { name: w.heading });
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[3]!);
    await screen.findByRole("heading", { name: w.form.heading });
    fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Another recap." } });
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await waitFor(() => expect(api.calls.filter((c) => c.startsWith("addRecap")).length).toBe(3));
    expect(screen.queryByLabelText(rx(w.form.right))).toBeNull(); // (the fresh page asks the API again; the mock now agrees)
  });

  it("asks before replacing a recap that is there, and Keep is a clean cancel", async () => {
    const api = await open();
    await fill(0);
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await screen.findByRole("alert"); // needs rights first
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    expect(await screen.findByText(w.askReplaceExisting)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.keep }));
    expect(screen.queryByRole("heading", { name: w.form.heading })).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: w.add })[0]!);
    await screen.findByRole("heading", { name: w.form.heading });
    fireEvent.input(screen.getByLabelText(rx(w.form.text)), { target: { value: "Their version." } });
    fireEvent.input(screen.getByLabelText(rx(w.form.who)), { target: { value: "Maren" } });
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    fireEvent.click(await screen.findByRole("button", { name: w.yesReplace }));
    await waitFor(() => expect(api.calls).toContain("addRecap:100.cmp1:1791000000:text:replace:rights"));
  });

  it("sends a Google Doc link as a link, and says when it is not one or is not shared", async () => {
    const api = await open();
    await fill(2, "https://example.com/elsewhere");
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await screen.findByRole("alert"); // needs rights
    fireEvent.click(screen.getByLabelText(rx(w.form.right)));
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["not-a-google-doc"]));
    fireEvent.input(screen.getByLabelText(rx(w.form.link_)), { target: { value: "https://docs.google.com/private" } });
    fireEvent.click(screen.getByRole("button", { name: w.form.submit }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["doc-not-shared"]));
    expect(api.calls.some((c) => c.includes(":link"))).toBe(true);
  });
});

describe("every code the routes answer, in plain words", () => {
  const codes: ApiErrorKind[] = ["no-lines", "busy", "not-in-plan", "too-soon", "bad-text", "too-big", "not-a-google-doc", "doc-not-shared", "doc-unreachable", "doc-too-big", "needs-rights", "not-allowed", "network", "server"];

  it.each(codes)("%s has words", (kind) => {
    expect(w.errors[kind]).toBeTruthy();
    expect(w.errors[kind]).not.toMatch(/_|\bAPI\b|\b4\d\d\b/);
  });

  it("an unknown code gets the general sentence, never the code", async () => {
    const api = mockApi("table");
    api.makeRecap = async () => {
      throw new ApiError("bad-page"); // (a kind this page has no words for)
    };
    await open("100.cmp1", api);
    fireEvent.click(screen.getByRole("button", { name: w.make }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.errors["server"]);
  });
});

describe("the real calls", () => {
  function recorded(status: number, body: unknown) {
    const seen: { url: string; init: RequestInit }[] = [];
    const api = httpApi("https://api.example", (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch);
    return { api, seen };
  }

  it("make it again posts {replace} to /me/recaps/{ref}/again", async () => {
    const { api, seen } = recorded(200, { ref: "100.cmp1.5", state: "queued", working: true });
    const got = await api.makeRecap!("100.cmp1.5", true);
    expect(seen[0]!.url).toBe("https://api.example/me/recaps/100.cmp1.5/again");
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({ replace: true });
    expect(got.ref).toBe("100.cmp1.5");
  });

  it("add posts the body to /me/campaigns/{ref}/recaps/upload", async () => {
    const { api, seen } = recorded(200, { ref: "100.cmp1.7", state: "ready", writtenBy: "Maren" });
    await api.addRecap!("100.cmp1", { session: 7, writtenBy: "Maren", text: "x", rights: true });
    expect(seen[0]!.url).toBe("https://api.example/me/campaigns/100.cmp1/recaps/upload");
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({ session: 7, writtenBy: "Maren", text: "x", rights: true });
  });

  it.each([
    [409, "needs_confirm", "needs-confirm"],
    [409, "busy", "busy"],
    [409, "no_lines", "no-lines"],
    [409, "needs_rights", "needs-rights"],
    [422, "not_a_google_doc", "not-a-google-doc"],
    [422, "doc_not_shared", "doc-not-shared"],
    [422, "doc_unreachable", "doc-unreachable"],
    [413, "doc_too_big", "doc-too-big"],
    [413, "too_big", "too-big"],
    [422, "bad_text", "bad-text"],
    [429, "too_soon", "too-soon"],
  ])("%i %s reads as %s", async (status, code, kind) => {
    const { api } = recorded(status, { error: code });
    await expect(api.makeRecap!("100.cmp1.5", false)).rejects.toMatchObject({ kind });
    await expect(api.addRecap!("100.cmp1", { session: 7, writtenBy: "M", text: "x" })).rejects.toMatchObject({ kind });
  });
});

describe("a recap that is already being made", () => {
  it("is watched when the page opens, and a failure can be closed to try again", async () => {
    const api = mockApi("table");
    const now = Math.floor(Date.now() / 1000);
    let looks = 0; // the first look finds it being made; after it failed, the list says so
    api.pastSessions = async () => ({
      sessions: [
        {
          ref: "100.cmp1.1790000000",
          sessionAt: 1790000000,
          sessionNumber: 3,
          sessionSeconds: 7200,
          hasTranscript: true,
          held: false,
          recap: { state: looks++ === 0 ? "prose" : "failed", status: "draft", working: looks === 1, approved: false, writtenBy: null, canMakeAgain: true },
        },
      ],
    });
    api.recap = async (ref) => ({
      ...(await mockApi("table").recap!("200000000000000001.cmp1.1791000000")),
      ref,
      working: false,
      state: "failed",
      error: "",
      progress: { stage: "prose", startedAt: now, updatedAt: now, serverNow: now, state: "failed", reason: "recap_failed" },
    });
    render(<RecapMake api={api} campaignRef="100.cmp1" />);
    expect(await screen.findByText(w.failedReason["recap_failed"] as string)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.close }));
    expect(await screen.findByRole("button", { name: w.again })).toBeTruthy();
  });
});

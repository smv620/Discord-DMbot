// The real client's addMap (#4347): the file goes as the body with the name and the person's own yes in headers, progress is heard
// while it goes and while DMbot saves it, and each refusal has its own kind.
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type MapUploadLook } from "../src/account/api";

class FakeXhr {
  static last: FakeXhr | null = null;
  static reply: { status: number; body: string } = { status: 202, body: '{"answer":"a1"}' };
  headers: Record<string, string> = {};
  method = "";
  url = "";
  withCredentials = false;
  status = 0;
  responseText = "";
  sent: unknown = null;
  upload: { onprogress: (() => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
    FakeXhr.last = this;
  }
  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value;
  }
  send(body: unknown): void {
    this.sent = body;
    queueMicrotask(() => {
      this.upload.onprogress?.();
      this.status = FakeXhr.reply.status;
      this.responseText = FakeXhr.reply.body;
      this.onload?.();
    });
  }
}

const file = new File([new Uint8Array([1, 2, 3])], "keep.png", { type: "image/png" });
const upload = { file, title: "The Keep, floor 1", rights: true as const };

function api(answers: object[]) {
  const asked: string[] = [];
  const fetcher = vi.fn(async (url: string) => {
    asked.push(url);
    const body = answers.shift() ?? { state: "done" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return { api: httpApi("https://api.test", fetcher as unknown as typeof fetch), asked };
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeXhr.reply = { status: 202, body: '{"answer":"a1"}' };
});

describe("the real addMap", () => {
  it("sends the file with the name and the yes, tells the page how it goes, and gives back the map and the Undo", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const { api: real, asked } = api([{ state: "working" }, { state: "done", mapId: "m9", batch: 12 }]);
    const looks: MapUploadLook[] = [];
    const made = await real.addMap!("100.cmp1", upload, (l) => looks.push(l));
    const x = FakeXhr.last!;
    expect(x.method).toBe("POST");
    expect(x.url).toBe("https://api.test/me/campaigns/100.cmp1/maps");
    expect(x.withCredentials).toBe(true);
    expect(x.headers["X-DMbot-Request"]).toBe("1");
    expect(x.headers["Content-Type"]).toBe("image/png");
    expect(decodeURIComponent(x.headers["X-Map-Title"]!)).toBe("The Keep, floor 1");
    expect(x.headers["X-Map-Rights"]).toBe("yes");
    expect(x.sent).toBe(file);
    expect(made).toEqual({ mapId: "m9", batch: 12 });
    expect(looks[0]?.stage).toBe("sending");
    expect(looks.at(-1)?.stage).toBe("saving");
    expect(asked[0]).toBe("https://api.test/me/campaigns/100.cmp1/web/answers/a1");
  });

  it("each refusal has its own kind", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const cases: [number, string, string][] = [
      [400, '{"error":"bad_file"}', "bad-file"],
      [413, "{}", "bad-file"],
      [400, '{"error":"needs_rights"}', "needs-rights"],
      [503, '{"error":"uploads_off"}', "uploads-off"],
      [429, '{"error":"too_soon"}', "too-soon"],
      [403, '{"error":"not_allowed"}', "not-allowed"],
      [401, "{}", "signed-out"],
    ];
    for (const [status, body, kind] of cases) {
      FakeXhr.reply = { status, body };
      await expect(api([]).api.addMap!("100.cmp1", upload)).rejects.toMatchObject({ kind });
    }
  });

  it("a failed send is a network failure, and a failed save is the bot's word", async () => {
    class Broken extends FakeXhr {
      override send(): void {
        queueMicrotask(() => this.onerror?.());
      }
    }
    vi.stubGlobal("XMLHttpRequest", Broken);
    await expect(api([]).api.addMap!("100.cmp1", upload)).rejects.toBeInstanceOf(ApiError);
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    await expect(api([{ state: "failed", error: "full" }]).api.addMap!("100.cmp1", upload)).rejects.toMatchObject({ kind: "full" });
  });
});

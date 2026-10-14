/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The unsubscribe page (#1229, #1231): the call is made by the page's script with the signed code
// from the link; anything missing or refused is said in words.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { unsubscribe } from "../src/content/unsubscribe";
import { Unsubscribe } from "../src/unsubscribe/Unsubscribe";

afterEach(cleanup);

const reply = (status: number): typeof fetch =>
  vi.fn(async () => new Response("{}", { status })) as unknown as typeof fetch;

describe("unsubscribe page", () => {
  it("sends the uid and code from the link, with the site header and no cookies", async () => {
    const fetcher = reply(200);
    render(<Unsubscribe search="?uid=ABC&t=xyz" base="https://api.example" fetcher={fetcher} />);
    expect(await screen.findByText(unsubscribe.done)).toBeTruthy();
    const [url, init] = (fetcher as unknown as { mock: { calls: [string, RequestInit][] } }).mock
      .calls[0]!;
    expect(url).toBe("https://api.example/email/unsubscribe");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("omit");
    expect(init.headers).toMatchObject({ "X-DMbot-Request": "1" });
    expect(JSON.parse(String(init.body))).toEqual({ uid: "ABC", t: "xyz" });
  });

  it("says the link didn't work when the API refuses it", async () => {
    render(<Unsubscribe search="?uid=ABC&t=bad" base="https://api.example" fetcher={reply(400)} />);
    expect(await screen.findByText(unsubscribe.bad)).toBeTruthy();
  });

  it("says so, and asks nothing, when the link is cut off", async () => {
    const fetcher = reply(200);
    for (const search of ["", "?uid=ABC", "?t=xyz"]) {
      render(<Unsubscribe search={search} base="https://api.example" fetcher={fetcher} />);
      await waitFor(() => expect(screen.getByText(unsubscribe.bad)).toBeTruthy());
      cleanup();
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("says so when the API can't be reached", async () => {
    const down = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    render(<Unsubscribe search="?uid=ABC&t=xyz" base="https://api.example" fetcher={down} />);
    expect(await screen.findByText(unsubscribe.bad)).toBeTruthy();
  });
});

/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The greeting for a personal link on /pricing and /hello (#1231): a valid ?uid=&t= shows the name
// and experience points; anything else shows nothing and, with no code, asks nothing.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { personal } from "../src/content/personal";
import { fetchPersonal } from "../src/personal/api";
import { Greeting } from "../src/personal/Greeting";

afterEach(cleanup);

const answer = (status: number, body: unknown): typeof fetch =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("personal greeting", () => {
  it("shows the name and the experience points for a valid link", async () => {
    const fetcher = answer(200, { name: "Belleros", experiencePoints: 135 });
    render(<Greeting search="?uid=ABC&t=1.2" base="https://api.example" fetcher={fetcher} />);
    expect(await screen.findByText(personal.hello("Belleros"))).toBeTruthy();
    expect(screen.getByText(personal.points(135))).toBeTruthy();
    const [url, init] = (fetcher as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0]!;
    expect(url).toBe("https://api.example/personal?uid=ABC&t=1.2");
    expect(init.credentials).toBe("omit"); // the code is all it needs
    expect(url).not.toContain("Belleros"); // the name is never in the address
  });

  it("shows only the points when the account has no name", async () => {
    render(
      <Greeting
        search="?uid=ABC&t=1.2"
        base="https://api.example"
        fetcher={answer(200, { name: null, experiencePoints: 0 })}
      />,
    );
    expect(await screen.findByText(personal.points(0))).toBeTruthy();
    expect(screen.queryByText(/^Hello,/)).toBeNull();
  });

  it("shows nothing for a wrong or ended link, a down API, or a mock build", async () => {
    for (const fetcher of [
      answer(404, { error: "not_found" }),
      answer(200, { nothing: true }),
      vi.fn(async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    ]) {
      const { container } = render(
        <Greeting search="?uid=ABC&t=1.2" base="https://api.example" fetcher={fetcher} />,
      );
      await waitFor(() => expect(fetcher).toHaveBeenCalled());
      expect(container.textContent).toBe("");
      cleanup();
    }
  });

  it("asks nothing and shows nothing without both uid and t", () => {
    const fetcher = answer(200, { name: "Belleros", experiencePoints: 1 });
    for (const search of ["", "?uid=ABC", "?t=1.2", "?demo=table"]) {
      const { container } = render(
        <Greeting search={search} base="https://api.example" fetcher={fetcher} />,
      );
      expect(container.textContent).toBe("");
      cleanup();
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("encodes what it sends", async () => {
    const fetcher = answer(404, {});
    await fetchPersonal("https://api.example/", "A B", "x&y", fetcher);
    const [url] = (fetcher as unknown as { mock: { calls: [string][] } }).mock.calls[0]!;
    expect(url).toBe("https://api.example/personal?uid=A+B&t=x%26y");
  });
});

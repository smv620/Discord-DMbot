/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A recap whose session's transcript was deleted by "Keep transcripts for" says so, quietly, in the
// list and on its page (#2022); the recap itself stays, and nothing links to the transcript.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { sessionDay, text } from "../src/content/recaps";

afterEach(cleanup);

const base = mockRecaps()[0]!;
const DELETED = base.sessionAt + 40 * 86400;

function withDeleted(extra: Partial<Recap>): AccountApi {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  const list = api.recaps!.bind(api);
  api.recap = async (ref) => ({ ...(await read(ref)), ...extra });
  api.recaps = async () => (await list()).map((r) => ({ ...r, ...extra }));
  return api;
}

describe("the words", () => {
  it("name the day and how long the table keeps transcripts", () => {
    expect(text.transcriptDeleted("3 March 2027", 90)).toBe(
      "Transcript deleted on 3 March 2027, as your table keeps transcripts for 90 days. The recap is kept.",
    );
    expect(text.transcriptDeleted("3 March 2027", 365)).toContain("for 1 year.");
  });

  it("leave the limit out when the table keeps transcripts forever, or an older API said nothing", () => {
    for (const days of [null, undefined]) {
      expect(text.transcriptDeleted("3 March 2027", days)).toBe(
        "Transcript deleted on 3 March 2027. The recap is kept.",
      );
    }
  });
});

describe("the recap pages", () => {
  const line = text.transcriptDeleted(sessionDay(DELETED), 90); // the recap's own page
  const short = text.transcriptDeleted(sessionDay(DELETED)); // the list
  const path = `/account/recaps/${base.ref}`;

  it("say it on the recap's own page, which still shows the recap and has no transcript link", async () => {
    render(
      <Recaps api={withDeleted({ transcriptsDeletedAt: DELETED, keepDays: 90 })} path={path} />,
    );
    expect(await screen.findByText(line)).toBeTruthy();
    expect(screen.getByText(/The party crossed/)).toBeTruthy(); // the recap stays
    expect(document.querySelector('a[href*="transcripts"]')).toBeNull();
  });

  it("say it in the list too", async () => {
    render(
      <Recaps
        api={withDeleted({ transcriptsDeletedAt: DELETED, keepDays: 90 })}
        path="/account/recaps"
      />,
    );
    expect((await screen.findAllByText(short)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/keeps transcripts/)).toBeNull(); // the reason is on the page only
  });

  it("say nothing when the transcript is still there, or the API is older than this", async () => {
    for (const extra of [{ transcriptsDeletedAt: null, keepDays: null }, {}]) {
      render(<Recaps api={withDeleted(extra)} path={path} />);
      await screen.findByText(/The party crossed/);
      expect(screen.queryByText(/Transcript deleted/)).toBeNull();
      cleanup();
    }
  });
});

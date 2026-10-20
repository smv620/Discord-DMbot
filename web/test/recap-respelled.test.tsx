/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A posted recap whose text a fixed spelling changed says so, and that Discord still shows the old
// spelling (#2178); a recap that was not respelled, or is not posted, says nothing.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountApi, Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { sessionDay, text } from "../src/content/recaps";

afterEach(cleanup);

const draft = mockRecaps()[0]!;
const posted = mockRecaps()[1]!;
const AT = posted.sessionAt + 3 * 86400;

function withRecap(extra: Partial<Recap>): AccountApi {
  const api = mockApi("table");
  const read = api.recap!.bind(api);
  api.recap = async (ref) => ({ ...(await read(ref)), ...extra });
  return api;
}

describe("the spelling note", () => {
  it("names the day and says Discord still shows the old spelling", () => {
    expect(text.respelled("3 March 2027")).toBe(
      "A name's spelling was fixed in this recap on 3 March 2027. The text on this page has the new spelling. The message already posted in Discord still has the old one.",
    );
  });

  it("shows on a posted recap that was respelled", async () => {
    render(<Recaps api={withRecap({ respelledAt: AT })} path={`/account/recaps/${posted.ref}`} />);
    expect(await screen.findByText(text.respelled(sessionDay(AT)))).toBeTruthy();
    expect(screen.getByText(`${text.respelledHeading}.`)).toBeTruthy();
  });

  it("is not there when the recap was never respelled", async () => {
    render(
      <Recaps api={withRecap({ respelledAt: null })} path={`/account/recaps/${posted.ref}`} />,
    );
    await screen.findByText(posted.text);
    expect(screen.queryByText(text.respelledHeading, { exact: false })).toBeNull();
  });

  it("is not there on a recap that is still a draft (nothing was posted to be wrong)", async () => {
    render(<Recaps api={withRecap({ respelledAt: AT })} path={`/account/recaps/${draft.ref}`} />);
    await screen.findByText(/The party crossed the Brynwater/);
    expect(screen.queryByText(text.respelledHeading, { exact: false })).toBeNull();
  });

  it("is not there on an API older than #2178", async () => {
    render(<Recaps api={withRecap({})} path={`/account/recaps/${posted.ref}`} />);
    await screen.findByText(posted.text);
    expect(screen.queryByText(text.respelledHeading, { exact: false })).toBeNull();
  });
});

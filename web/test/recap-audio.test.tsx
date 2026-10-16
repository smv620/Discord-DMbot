/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The narrated-recaps add-on on the site (#1390): the pricing row, My Account's three states and
// the line on the recap page. All of it shows only while RECAP_AUDIO_LIVE is true (false today).
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import {
  ApiError,
  type AccountApi,
  type Me,
  type RecapAudio,
} from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import {
  accountWords,
  priceText,
  pricingRow,
  RECAP_AUDIO_PRICE_CENTS,
} from "../src/content/recap-audio";
import { mockRecaps } from "../src/account/mock";

// A switch the tests can flip; the real constants are false.
const live = vi.hoisted(() => ({ audio: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  cleanup();
  live.audio = false;
});

function withAudio(audio: RecapAudio): AccountApi & { calls: string[] } {
  const api = mockApi("table");
  const me = api.me.bind(api);
  api.me = async (): Promise<Me | null> => {
    const m = await me();
    return m && { ...m, recapAudio: audio };
  };
  return api;
}

const included: RecapAudio = { included: true, addOn: false, canBuy: false };
const bought: RecapAudio = {
  included: false,
  addOn: true,
  endsOn: "2026-11-15",
  canBuy: false,
};
const buyable: RecapAudio = { included: false, addOn: false, canBuy: true };
const unavailable: RecapAudio = {
  included: false,
  addOn: false,
  canBuy: false,
};

describe("the price", () => {
  it("is not set today, so no price is shown anywhere", () => {
    expect(RECAP_AUDIO_PRICE_CENTS).toBeNull();
    expect(pricingRow.priceLine).toBeNull();
    expect(accountWords.buy).toBe("Add narrated recaps");
  });

  it("is said as dollars a month once it is set", () => {
    expect(priceText(null)).toBeNull();
    expect(priceText(499)).toBe("$4.99 a month");
  });
});

describe("My Account", () => {
  it("shows nothing about it while the audio is off", async () => {
    render(<Account api={withAudio(buyable)} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByText(accountWords.heading)).toBeNull();
  });

  it("Included: says so, with no button", async () => {
    live.audio = true;
    render(<Account api={withAudio(included)} go={vi.fn()} />);
    expect(await screen.findByText(accountWords.included)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Add narrated recaps/ }),
    ).toBeNull();
  });

  it("Bought: says until when, and how to stop it", async () => {
    live.audio = true;
    render(<Account api={withAudio(bought)} go={vi.fn()} />);
    expect(
      await screen.findByText(accountWords.addOn("2026-11-15")),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Add narrated recaps/ }),
    ).toBeNull();
  });

  it("Buy: a button that goes to the payment company's page", async () => {
    live.audio = true;
    const go = vi.fn();
    const api = withAudio(buyable);
    render(<Account api={api} go={go} />);
    fireEvent.click(
      await screen.findByRole("button", { name: accountWords.buy }),
    );
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-recap-audio"));
    expect(api.calls).toContain("recap-audio");
    expect(screen.getByText(accountWords.buyNote(null))).toBeTruthy();
  });

  it("not for sale: no section at all", async () => {
    live.audio = true;
    render(<Account api={withAudio(unavailable)} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    expect(
      screen.queryByRole("button", { name: /Add narrated recaps/ }),
    ).toBeNull();
  });

  it("says why when the API refuses because the plan has it now", async () => {
    live.audio = true;
    const api = withAudio(buyable);
    api.recapAudioUrl = async () => {
      throw new ApiError("already-subscribed");
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: accountWords.buy }),
    );
    expect(
      await screen.findByText(accountWords.errors["already-subscribed"]),
    ).toBeTruthy();
  });
});

describe("the recap page", () => {
  const path = `/account/recaps/${mockRecaps()[0]!.ref}`;

  it("says the plan has no narration, and offers it, where it is not included", async () => {
    live.audio = true;
    render(<Recaps api={withAudio(buyable)} path={path} />);
    expect(await screen.findByText(accountWords.notIncluded)).toBeTruthy();
    expect(screen.getByRole("button", { name: accountWords.buy })).toBeTruthy();
  });

  it("says nothing when it cannot be bought (no dead end)", async () => {
    live.audio = true;
    render(<Recaps api={withAudio(unavailable)} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(accountWords.notIncluded)).toBeNull();
  });

  it("says nothing where it is included or bought", async () => {
    live.audio = true;
    for (const audio of [included, bought]) {
      render(<Recaps api={withAudio(audio)} path={path} />);
      await screen.findByText(/The party crossed/);
      expect(screen.queryByText(accountWords.notIncluded)).toBeNull();
      cleanup();
    }
  });

  it("says nothing while the audio is off", async () => {
    render(<Recaps api={withAudio(buyable)} path={path} />);
    await screen.findByText(/The party crossed/);
    expect(screen.queryByText(accountWords.notIncluded)).toBeNull();
  });
});

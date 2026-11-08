/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph panels show DMbot's stage while a change is out (#3803): a press holds the change, the stage and how long
// it has run show, and they go when the change is answered. Against plain stubs, so each panel's own wait is what is tested.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { NameRow, WebNode } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import PcIconRow from "../src/account/PcIconRow";
import PlaceSpellings from "../src/account/PlaceSpellings";
import PlayersLine from "../src/account/PlayersLine";
import { placeSpellingWords as place } from "../src/content/place-spelling-words";
import { playersLineWords as line } from "../src/content/players-line-words";
import { savingWait } from "../src/content/work-progress";

afterEach(cleanup);

/** A promise the test resolves itself, so the change stays out for as long as the test wants. */
function held<T>(): { promise: Promise<T>; finish: (value: T) => void } {
  let finish!: (value: T) => void;
  const promise = new Promise<T>((resolve) => (finish = resolve));
  return { promise, finish };
}

describe("the Story Graph panels show DMbot's stage while a change is out (#3803)", () => {
  it("the icon row shows the stage while the icon is being changed", async () => {
    const pending = held<null>();
    const node = { id: "n1", name: "Ken", kind: "npc", icon: "default" } as unknown as WebNode;
    render(<PcIconRow node={node} onChoose={() => pending.promise} undo={false} onUndo={async () => null} />);
    fireEvent.click(screen.getByRole("button", { name: /Hide the icon/ }));
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => pending.finish(null));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });

  it("the players line shows the stage while the line is being saved", async () => {
    const pending = held<null>();
    const row = { id: "n1", name: "Ken", playersText: "" } as unknown as NameRow;
    render(<PlayersLine row={row} act={() => pending.promise} />);
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "He is kind." } });
    fireEvent.click(screen.getByRole("button", { name: line.save }));
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => pending.finish(null));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });

  it("the place spellings show the stage while a yes or no is out", async () => {
    const api = mockApi("table");
    const pending = held<void>();
    api.answerPlaceSpelling = () => pending.promise;
    render(<PlaceSpellings api={api} campaignRef="100.cmp1" onSignedOut={() => {}} onChanged={() => {}} />);
    const yes = await screen.findAllByRole("button", { name: /^Yes, / });
    fireEvent.click(yes[0] as HTMLElement);
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => pending.finish(undefined));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
    expect(place.saidYes).toBeTruthy();
  });
});

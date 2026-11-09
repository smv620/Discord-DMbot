/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph panels show DMbot's stage while a change is out (#3803): a press holds the change, the stage and how long
// it has run show, and they go when the change is answered. Against plain stubs, so each panel's own wait is what is tested.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { NameRow, WebEdge, WebLink, WebNode } from "../src/account/api";
import LinkFeeling from "../src/account/LinkFeeling";
import { mockApi } from "../src/account/mock";
import PcIconRow from "../src/account/PcIconRow";
import PlaceSpellings from "../src/account/PlaceSpellings";
import PlayersLine from "../src/account/PlayersLine";
import WebLinePanel from "../src/account/WebLinePanel";
import WebNodePanel from "../src/account/WebNodePanel";
import { FEELINGS } from "../src/content/knows-feeling-words";
import { feelingWords as feel } from "../src/content/knows-feeling-text";
import { placeSpellingWords as place } from "../src/content/place-spelling-words";
import { playersLineWords as line } from "../src/content/players-line-words";
import { savingWait } from "../src/content/work-progress";
import { webPage as pageWords } from "../src/content/web-words";

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

describe("the link and name panels show DMbot's stage while a change is out (#3803)", () => {
  it("the feeling picker shows the stage while the feeling is being saved", async () => {
    const pending = held<string | null>();
    const link = { id: "l1", words: "knows", secret: false, direction: "a-b", source: null, quote: null } as unknown as WebLink;
    render(<LinkFeeling n={1} link={link} feeling={FEELINGS[0]!} act={() => pending.promise} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: FEELINGS[1]! } });
    fireEvent.click(screen.getByRole("button", { name: feel.saveFor(1) }));
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    await act(async () => pending.finish(null));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });

  it("a connection's reveal shows the stage below its row while it is out", async () => {
    const link = { id: "l1", words: "trusts", secret: true, direction: "a-b", revealed: false } as unknown as WebLink;
    const edge = { a: "a", b: "b", label: "trusts", more: 0, links: [link] } as WebEdge;
    render(
      <WebLinePanel
        edge={edge}
        nameOf={(id) => id}
        kindOf={() => "npc"}
        canEdit
        act={async () => null}
        reveal={() => new Promise(() => {})}
        revealing={new Set(["link:l1"])}
        api={mockApi("table")}
        refId="100.cmp1"
        onClose={() => {}}
      />,
    );
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
  });

  it("the name editor shows the stage while a save is out, and a second press does nothing", async () => {
    const pending = held<string | null>();
    let calls = 0;
    const node = { id: "n1", name: "Ken", kind: "npc" } as unknown as WebNode;
    render(
      <WebNodePanel
        node={node}
        act={() => {
          calls += 1;
          return pending.promise;
        }}
        api={mockApi("table")}
        refId="100.cmp1"
      />,
    );
    const save = screen.getByRole("button", { name: pageWords.saveSpelling });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(await screen.findByText(savingWait.stage)).toBeTruthy();
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(calls).toBe(1);
    await act(async () => pending.finish(null));
    expect(screen.queryByText(savingWait.stage)).toBeNull();
  });
});

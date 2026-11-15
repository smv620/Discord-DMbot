/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Add a map" form on the Maps page (#4347, slice B): nothing is sent without the yes, the picture and the name are checked before
// sending, each refusal shows its words, and a second press while it waits sends once.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Maps from "../src/account/Maps";
import { mapsWords as w } from "../src/content/maps-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/maps-switch", () => ({
  get MAPS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const DM = "100.cmp1";
const PLAYER = "100.cmp1.player";
const picture = (name = "keep.png", size = 3) => new File([new Uint8Array(size)], name, { type: "image/png" });

async function choose(file: File, name: string) {
  fireEvent.change(screen.getByLabelText(w.chooseFile), { target: { files: [file] } });
  fireEvent.input(screen.getByLabelText(w.nameIt), { target: { value: name } });
}

describe("the Add a map form", () => {
  it("shows the form under the list only when addMap exists", async () => {
    render(<Maps api={mockApi("table")} refId={DM} />);
    expect(await screen.findByRole("heading", { name: w.addHeading })).toBeTruthy();
    cleanup();
    const api = mockApi("table");
    delete api.addMap;
    render(<Maps api={api} refId={DM} />);
    await screen.findAllByText("Cave");
    expect(screen.queryByRole("heading", { name: w.addHeading })).toBeNull();
  });

  it("yes is disabled until a file and a name are given", async () => {
    render(<Maps api={mockApi("table")} refId={DM} />);
    const yes = await screen.findByRole("button", { name: w.yesLabel });
    expect((yes as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(w.chooseFile), { target: { files: [picture()] } });
    expect((yes as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(screen.getByLabelText(w.nameIt), { target: { value: "   " } });
    expect((yes as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(screen.getByLabelText(w.nameIt), { target: { value: "Mill" } });
    expect((yes as HTMLButtonElement).disabled).toBe(false);
  });

  it("a DM's map is added, shows addedDm, appears in the list, and Undo removes it", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    await screen.findByText(w.addedDm);
    expect(api.calls.some((c) => c === `addMap:${DM}:Mill`)).toBe(true);
    expect(await screen.findByText("Mill")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    await screen.findByText(w.undone);
    await waitFor(() => expect(screen.queryByText("Mill")).toBeNull());
  });

  it("a player's map shows addedPlayer", async () => {
    render(<Maps api={mockApi("table")} refId={PLAYER} />);
    await screen.findByText("Harbour sketch");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.addedPlayer)).toBeTruthy();
  });

  it("no empties the form, shows noSent and sends nothing", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.noLabel }));
    expect(await screen.findByText(w.noSent)).toBeTruthy();
    expect((screen.getByLabelText(w.nameIt) as HTMLInputElement).value).toBe("");
    expect(api.calls.some((c) => c.startsWith("addMap:"))).toBe(false);
  });

  it("a file over 10 MB is refused before sending", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture("big.png", 10 * 1024 * 1024 + 1), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.badFile(10))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("addMap:"))).toBe(false);
  });

  it("a bad-file refusal shows badFile, and any other failure shows network", async () => {
    const api = mockApi("table");
    api.addMap = async () => {
      throw new ApiError("bad-file");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.badFile(10))).toBeTruthy();
    api.addMap = async () => {
      throw new ApiError("server");
    };
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.network)).toBeTruthy();
    expect((screen.getByLabelText(w.nameIt) as HTMLInputElement).value).toBe("Mill"); // the form keeps its values
  });

  it("a second press while it waits sends once", async () => {
    const api = mockApi("table");
    const real = api.addMap!.bind(api);
    let release!: () => void;
    api.addMap = async (ref, upload, onLook) => {
      await new Promise<void>((r) => (release = r));
      return real(ref, upload, onLook);
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    const yes = screen.getByRole("button", { name: w.yesLabel }) as HTMLButtonElement;
    yes.click();
    yes.click();
    release();
    await screen.findByText(w.addedDm);
    expect(api.calls.filter((c) => c.startsWith("addMap:")).length).toBe(1);
  });
});

describe("the Add a map form's progress and Undo", () => {
  it("shows what stage the upload is at, the seconds since it last moved, and that it has stalled", async () => {
    const api = mockApi("table");
    const real = api.addMap!.bind(api);
    let release!: () => void;
    api.addMap = async (ref, upload, onLook) => {
      onLook?.({ stage: "sending", seconds: 3, quiet: 5 });
      expect(await screen.findByText(w.sending(3))).toBeTruthy();
      expect(await screen.findByText(w.lastMoved(5))).toBeTruthy();
      onLook?.({ stage: "saving", seconds: 40, quiet: 30 });
      await new Promise<void>((r) => (release = r));
      return real(ref, upload, onLook);
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.stalled(40, "30 seconds"))).toBeTruthy();
    release();
    await screen.findByText(w.addedDm);
  });

  it("says it went wrong when Undo cannot be sent, and keeps the page", async () => {
    const api = mockApi("table");
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    await screen.findByText(w.addedDm);
    api.changeMaps = async () => {
      throw new ApiError("server");
    };
    fireEvent.click(screen.getByRole("button", { name: w.undoLabel }));
    expect(await screen.findByText(w.wrong)).toBeTruthy();
  });

  it("clears an earlier refusal when a new picture is chosen", async () => {
    const api = mockApi("table");
    api.addMap = async () => {
      throw new ApiError("bad-file");
    };
    render(<Maps api={api} refId={DM} />);
    await screen.findByText("Cave");
    await choose(picture(), "Mill");
    fireEvent.click(screen.getByRole("button", { name: w.yesLabel }));
    expect(await screen.findByText(w.badFile(10))).toBeTruthy();
    fireEvent.change(screen.getByLabelText(w.chooseFile), { target: { files: [picture("other.png")] } });
    expect(screen.queryByText(w.badFile(10))).toBeNull();
  });
});

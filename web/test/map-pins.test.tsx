/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Pins and scale" panel under a map (#4672): the pins drawn on the picture, what DMbot knows of the scale, grid and north, and every
// mark with its target. A DM only, behind MAP_PINS_LIVE; the Maps page shows its button only then.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NAMES_METHODS, type MapItem } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Maps from "../src/account/Maps";
import MapPins from "../src/account/MapPins";
import { mapPinsWords as w } from "../src/content/map-pins-words";

const pins = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/map-pins-switch", () => ({
  get MAP_PINS_LIVE() {
    return pins.on;
  },
}));
const maps = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/maps-switch", () => ({
  get MAPS_LIVE() {
    return maps.on;
  },
}));

afterEach(() => {
  cleanup();
  pins.on = false;
  maps.on = true;
});

const REF = "100.cmp1";
const DM_REF = "100.cmp1";

async function firstMap(ref = REF): Promise<MapItem> {
  return (await mockApi("table").maps!(ref)).maps[0]!;
}

async function open(ref = REF) {
  const api = mockApi("table");
  const map = await firstMap(ref);
  render(<MapPins api={api} campaignRef={ref} map={map} />);
  return { api, map };
}

describe("the Pins and scale panel", () => {
  it("draws a pin at its place on the picture and lists every mark with its target", async () => {
    await open();
    const targos = await screen.findByTitle("Targos");
    expect(targos.getAttribute("style")).toContain("left: 20%");
    expect(targos.getAttribute("style")).toContain("top: 50%");
    expect(screen.getByText("Targos: Pin")).toBeTruthy();
    expect(screen.getAllByText(w.place("Targos")).length).toBeGreaterThan(0);
  });

  it("a mark with no target says it is not matched to a name yet", async () => {
    await open();
    await screen.findByText("Kelvin's Cairn: Pin");
    expect(screen.getByText(w.unmatched)).toBeTruthy();
  });

  it("a DM's own pin looks different from a read one", async () => {
    await open();
    const own = await screen.findByTitle("Easthaven");
    const read = screen.getByTitle("Targos");
    expect(own.getAttribute("style")).toMatch(/background: currentcolor/i);
    expect(read.getAttribute("style")).toContain("background: transparent");
  });

  it("paths and areas are listed and not drawn", async () => {
    await open();
    expect(await screen.findByText("Road: Path")).toBeTruthy();
    expect(screen.queryByTitle("Road")).toBeNull();
    expect(screen.getByText(w.road("Bryn Shander to Targos"))).toBeTruthy();
  });

  it("says there is no scale when there is none, and where a known one came from", async () => {
    await open(`${REF}.pins-none`);
    expect(await screen.findByText(w.noScale)).toBeTruthy();
    cleanup();
    await open();
    expect(await screen.findByText(w.scale("miles", w.from.read))).toBeTruthy();
  });

  it("says north is up when it is assumed, and how far it is turned when it is read", async () => {
    await open(`${REF}.pins-none`);
    expect(await screen.findByText(w.northAssumed)).toBeTruthy();
    cleanup();
    await open();
    expect(await screen.findByText(w.north(90, w.from.read))).toBeTruthy();
  });

  it("shows no grid line when there is no grid", async () => {
    await open(`${REF}.pins-none`);
    await screen.findByText(w.northAssumed);
    expect(screen.queryByText(/^Grid:/)).toBeNull();
    cleanup();
    await open();
    expect(await screen.findByText(w.grid("square", w.from.dm))).toBeTruthy();
  });

  it("says nothing was found when there are no marks", async () => {
    await open(`${REF}.pins-none`);
    expect(await screen.findByText(w.none)).toBeTruthy();
  });

  it("says it could not load when the read fails", async () => {
    await open(`${REF}.pins-fail`);
    expect(await screen.findByText(w.failed)).toBeTruthy();
  });
});

describe("the Maps page's button for the panel", () => {
  it("the Maps page shows the button only for a DM with the switch on", async () => {
    pins.on = false;
    render(<Maps api={mockApi("table")} refId={DM_REF} />);
    await screen.findByText("Icewind Dale");
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
    cleanup();
    pins.on = true;
    render(<Maps api={mockApi("table")} refId={DM_REF} />);
    await screen.findByText("Icewind Dale");
    fireEvent.click((await screen.findAllByRole("button", { name: w.button }))[0]!);
    expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
    await waitFor(() => expect(screen.getByText(w.privacy)).toBeTruthy());
  });

  it("the api-areas list includes mapFeatures", () => {
    expect(NAMES_METHODS).toContain("mapFeatures");
  });

  it("a read the API refuses with not-allowed says only a DM can see it", async () => {
    const api = mockApi("table");
    api.mapFeatures = async () => {
      throw new ApiError("not-allowed");
    };
    render(<MapPins api={api} campaignRef={REF} map={await firstMap()} />);
    expect(await screen.findByText(w.notDm)).toBeTruthy();
  });
});

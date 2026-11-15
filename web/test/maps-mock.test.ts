// The pretend Maps API (#4347): a DM sees every map, a player only what the table may see plus their own, and every change can be undone.
import { describe, expect, it } from "vitest";

import type { MapUploadLook } from "../src/account/api";
import { mockApi } from "../src/account/mock";

const DM = "100.cmp1";
const PLAYER = "100.cmp1.player";

describe("the pretend Maps API", () => {
  it("a DM sees every map with its state and shown", async () => {
    const api = mockApi("table");
    const read = await api.maps!(DM);
    expect(read.canManage).toBe(true);
    expect(read.maps.map((m) => m.id)).toEqual(["m1", "m2", "m3", "m4", "m5"]);
    expect(read.maps.find((m) => m.id === "m1")).toMatchObject({ shown: true, state: "usable" });
    expect(read.maps.find((m) => m.id === "m3")).toMatchObject({ mine: true, state: "waiting" });
  });

  it("a player sees the shown usable maps and their own, and state only on their own", async () => {
    const api = mockApi("table");
    const read = await api.maps!(PLAYER);
    expect(read.canManage).toBe(false);
    expect(read.maps.map((m) => m.id)).toEqual(["m1", "m3"]);
    const shown = read.maps.find((m) => m.id === "m1")!;
    expect("shown" in shown).toBe(false);
    expect("state" in shown).toBe(false);
    expect(read.maps.find((m) => m.id === "m3")).toMatchObject({ mine: true, shown: false, state: "waiting" });
  });

  it("a player never gets a hidden map they did not add", async () => {
    const api = mockApi("table");
    const ids = (await api.maps!(PLAYER)).maps.map((m) => m.id);
    expect(ids).not.toContain("m2"); // hidden and usable
    expect(ids).not.toContain("m4"); // hidden and declined
    expect(ids).not.toContain("m5"); // hidden and waiting
  });

  it("use only answers a waiting map, and undo puts it back", async () => {
    const api = mockApi("table");
    const { batch } = await api.changeMaps!(DM, { action: "use", id: "m5", yes: true });
    expect((await api.maps!(DM)).maps.find((m) => m.id === "m5")?.state).toBe("usable");
    await expect(api.changeMaps!(DM, { action: "use", id: "m5", yes: false })).rejects.toMatchObject({ kind: "gone" });
    await api.changeMaps!(DM, { action: "undo", batch: batch! });
    expect((await api.maps!(DM)).maps.find((m) => m.id === "m5")?.state).toBe("waiting");
  });

  it("show, then undo, restores", async () => {
    const api = mockApi("table");
    const { batch } = await api.changeMaps!(DM, { action: "show", id: "m2", shown: true });
    expect((await api.maps!(DM)).maps.find((m) => m.id === "m2")?.shown).toBe(true);
    await api.changeMaps!(DM, { action: "undo", batch: batch! });
    expect((await api.maps!(DM)).maps.find((m) => m.id === "m2")?.shown).toBe(false);
  });

  it("takeback works only on your own map", async () => {
    const api = mockApi("table");
    await expect(api.changeMaps!(DM, { action: "takeback", id: "m1" })).rejects.toMatchObject({ kind: "not-allowed" });
    expect((await api.maps!(DM)).maps.map((m) => m.id)).toContain("m1");
    await api.changeMaps!(DM, { action: "takeback", id: "m3" });
    expect((await api.maps!(DM)).maps.map((m) => m.id)).not.toContain("m3");
  });

  it("addMap reports sending then saving, then adds the map", async () => {
    const api = mockApi("table");
    const looks: MapUploadLook[] = [];
    const file = new File([new Uint8Array([1, 2, 3])], "keep.png", { type: "image/png" });
    const made = await api.addMap!(PLAYER, { file, title: "Mill", rights: true }, (look) => looks.push(look));
    expect(looks.map((l) => l.stage)).toEqual(["sending", "saving"]);
    expect(made.mapId).toMatch(/^m\d+$/);
    const row = (await api.maps!(PLAYER)).maps.find((m) => m.id === made.mapId);
    expect(row).toMatchObject({ title: "Mill", mine: true, state: "waiting" });
  });
});

describe("the pretend Maps writes, by a player", () => {
  it("a player cannot use, show or remove a map, and a map hidden from them reads as gone", async () => {
    const api = mockApi("table");
    await expect(api.changeMaps!(PLAYER, { action: "show", id: "m3", shown: true })).rejects.toMatchObject({ kind: "not-allowed" });
    await expect(api.changeMaps!(PLAYER, { action: "use", id: "m3", yes: true })).rejects.toMatchObject({ kind: "not-allowed" });
    await expect(api.changeMaps!(PLAYER, { action: "remove", id: "m1" })).rejects.toMatchObject({ kind: "not-allowed" });
    await expect(api.changeMaps!(PLAYER, { action: "show", id: "m2", shown: true })).rejects.toMatchObject({ kind: "gone" });
    await expect(api.changeMaps!(PLAYER, { action: "remove", id: "m4" })).rejects.toMatchObject({ kind: "gone" });
  });

  it("undo of a remove and of a take-back puts the map back", async () => {
    const api = mockApi("table");
    const removed = await api.changeMaps!(DM, { action: "remove", id: "m1" });
    expect((await api.maps!(DM)).maps.map((m) => m.id)).not.toContain("m1");
    await api.changeMaps!(DM, { action: "undo", batch: removed.batch! });
    expect((await api.maps!(DM)).maps.map((m) => m.id)).toContain("m1");
    const taken = await api.changeMaps!(DM, { action: "takeback", id: "m3" });
    await api.changeMaps!(DM, { action: "undo", batch: taken.batch! });
    expect((await api.maps!(DM)).maps.map((m) => m.id)).toContain("m3");
  });
});

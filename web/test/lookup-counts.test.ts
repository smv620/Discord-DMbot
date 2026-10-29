// "What DMbot can look up" on the Credits page (#1932): counts read from the rules data, every kind
// in the data has a plain name, and the page shows what the files hold.
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { describe, expect, it } from "vitest";

import { countByKind, kindNames, lookupList, readFolder, unnamedKinds } from "../scripts/lookup-counts.mjs";
import { sources } from "../src/content/credits";
import credits from "../src/pages/credits.astro";

const dataDir = (folder: string): string =>
  fileURLToPath(new URL(`../../core/src/dmbot/rules/data/${folder}`, import.meta.url));

describe("lookup counts", () => {
  it("gives every kind in the data a plain name", () => {
    for (const s of sources) expect(unnamedKinds(readFolder(dataDir(s.folder))), s.folder).toEqual([]);
  });

  it("fails on a kind with no plain name, so it can't show up under its data name", () => {
    const files = [{ kind: "vehicle", entries: [{}, {}] }];
    expect(unnamedKinds(files)).toEqual(["vehicle"]);
    expect(() => lookupList(files)).toThrow(/no plain name for the kind: vehicle/);
  });

  it("counts the entries of each file by its kind, or the entry's own", () => {
    const files = [
      { kind: "spell", entries: [{}, {}, {}] },
      { kind: "origin", entries: [{ kind: "species" }, { kind: "species" }, { kind: "background" }] },
    ];
    expect([...countByKind(files)]).toEqual([["spell", 3], ["species", 2], ["background", 1]]);
    expect(lookupList(files)).toEqual([
      { label: "Spells", count: 3 },
      { label: "Species", count: 2 },
      { label: "Backgrounds", count: 1 },
    ]);
  });

  it("matches the number of entries in the data files, kind by kind", () => {
    for (const s of sources) {
      const dir = dataDir(s.folder);
      const byHand = new Map<string, number>();
      for (const name of readdirSync(dir).filter((n) => n.endsWith(".json"))) {
        const file = JSON.parse(readFileSync(`${dir}/${name}`, "utf8")) as { kind: string; entries: { kind?: string }[] };
        for (const e of file.entries) byHand.set(e.kind ?? file.kind, (byHand.get(e.kind ?? file.kind) ?? 0) + 1);
      }
      for (const { label, count } of lookupList(readFolder(dir))) {
        const kind = Object.entries(kindNames).find(([, l]) => l === label)?.[0] ?? "";
        expect(count, `${s.folder} ${label}`).toBe(byHand.get(kind));
      }
    }
  });

  it("names the equipment kinds, and counts them from the equipment file (#1930)", () => {
    expect(kindNames.tool).toBe("Tools");
    expect(kindNames.gear).toBe("Gear, mounts and vehicles");
    const newer = lookupList(readFolder(dataDir("srd52")));
    const labels = newer.map((k) => k.label);
    expect(labels.indexOf("Armor")).toBeLessThan(labels.indexOf("Tools"));
    expect(labels.indexOf("Tools")).toBeLessThan(labels.indexOf("Gear, mounts and vehicles"));
    const equipment = JSON.parse(readFileSync(`${dataDir("srd52")}/equipment.json`, "utf8")) as { entries: { kind: string }[] };
    for (const [kind, label] of [["tool", "Tools"], ["gear", "Gear, mounts and vehicles"]] as const) {
      expect(newer.find((k) => k.label === label)?.count, label).toBe(equipment.entries.filter((e) => e.kind === kind).length);
    }
  });

  it("names the game master's kinds: poisons, traps, curses and diseases (#1935, #1944)", () => {
    const files = [
      { kind: "toolbox", entries: [{ kind: "poison" }, { kind: "trap" }, { kind: "trap" }, { kind: "curse" }, { kind: "disease" }] },
    ];
    expect(unnamedKinds(files)).toEqual([]);
    expect(lookupList(files)).toEqual([
      { label: "Poisons", count: 1 },
      { label: "Traps", count: 2 },
      { label: "Curses", count: 1 },
      { label: "Diseases", count: 1 },
    ]);
  });

  it("names gear and tool entries from a made-up file, whatever the data holds today", () => {
    const files = [{ kind: "equipment", entries: [{ kind: "gear" }, { kind: "gear" }, { kind: "tool" }] }];
    expect(lookupList(files)).toEqual([
      { label: "Tools", count: 1 },
      { label: "Gear, mounts and vehicles", count: 2 },
    ]);
  });

  it("names the hazard kinds the Gameplay Toolbox adds (#1935)", () => {
    expect(kindNames.poison).toBe("Poisons");
    expect(kindNames.trap).toBe("Traps");
    expect(kindNames.curse).toBe("Curses");
    const labels = lookupList(readFolder(dataDir("srd52"))).map((k) => k.label);
    expect(labels).toEqual(expect.arrayContaining(["Poisons", "Traps", "Curses"]));
  });

  it("lists only the kinds a SRD has, read from its data", () => {
    for (const s of sources) {
      const files = readFolder(dataDir(s.folder));
      const present = new Set(countByKind(files).keys());
      const listed = lookupList(files).map((k) => k.label);
      for (const [kind, label] of Object.entries(kindNames)) {
        expect(listed.includes(label), `${s.folder} ${label}`).toBe(present.has(kind));
      }
    }
    expect(lookupList(readFolder(dataDir("srd51"))).map((k) => k.label)).toContain("Spells");
  });

  it("shows the counts on the Credits page, under each SRD", async () => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(credits, { request: new Request("https://dmbot.example/credits") });
    for (const s of sources) {
      for (const { label, count } of lookupList(readFolder(dataDir(s.folder)))) {
        expect(html, `${s.folder} ${label}`).toContain(`${label}: ${count}`);
      }
    }
    expect(html).toContain("What DMbot can look up");
  });
});

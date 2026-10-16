// @ts-check
// The build `npm run check:smoke` serves (#1488): the same site, with the pretend API
// (PUBLIC_API_BASE=mock, set by the script) and the recap pages switched on, so the browser run can
// reach what the real site keeps hidden until the owner turns recaps on. It builds into
// dist-smoke/, never dist/, and is never deployed. The switches are flipped here, in the build,
// so no source file carries a smoke-only branch.
import base from "./astro.config.mjs";

const SWITCHES = ["RECAPS_LIVE", "RECAP_AUDIO_LIVE"];

/** @type {import("vite").Plugin} */
const switchRecapsOn = {
  name: "smoke-recap-switches",
  enforce: "pre",
  transform(code, id) {
    if (!/\/content\/recap-words\.ts$/.test((id.split("?")[0] ?? ""))) return null;
    let out = code;
    for (const name of SWITCHES) {
      const off = `export const ${name} = false;`;
      // Fail the build rather than smoke-test a site where the switch is still off.
      if (!out.includes(off)) throw new Error(`smoke build: "${off}" not found in recap-words.ts`);
      out = out.replace(off, `export const ${name} = true;`);
    }
    return { code: out, map: null };
  },
};

export default {
  ...base,
  outDir: "dist-smoke",
  vite: { ...base.vite, plugins: [...(base.vite?.plugins ?? []), switchRecapsOn] },
};

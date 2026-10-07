# DMbot website (`web/`)

The customer website: static pages (home, prices, legal, how to add DMbot) and one small
signed-in area (`/account`). Built with [Astro](https://astro.build) and TypeScript in
strict mode. Built and maintained by the **WebDev** session.

The site holds no plan rules and no campaign data. The signed-in area talks to the web API
in core (`core/src/dmbot/web/`, FastAPI, its own container), because the plan rules and
campaign isolation live there and must not be copied here.

## Run it

Node 22 (`nvm use` reads `.nvmrc`), npm 10.

```bash
cd web
npm ci
npm run dev        # http://localhost:4321, reloads on save
npm run typecheck  # astro check: TypeScript and .astro files
npm test           # Vitest: one smoke test per page, plus the trademark check
npm run build      # static site in dist/
npm run preview    # serve dist/ to check the build
```

CI runs `typecheck`, `test` and `build` in the `web` job on every push.

## Layout

- `src/layouts/Base.astro`: the one layout (head tags, menu, footer with legal links).
- `src/styles/global.css`: the design tokens (colors, type scale, spacing) and base styles.
  Light and dark follow the device setting. Change colors here, never in a page.
- `src/pages/`: one file per page. `/account` is the signed-in area and is kept out of
  search results.
- `test/`: Vitest smoke tests.

Interactivity, when a page needs it, goes in a Preact island; there is no UI framework
until then.

## Rules for every page

- **No trademarks or art of the game or its publisher.** Never write "D&D", "Dungeons &
  Dragons", "Wizards of the Coast", "D&D Beyond", or the names of their books or worlds,
  and never use their logos or art. Say **"for 5e-compatible tabletop games"**. Example
  material uses our own invented names, the bake-off cast in `docs/test-scripts/`
  (Belleros, Oskar Vane, Brynwater, Gorrak, …). `npm test` fails if a banned name appears
  in `src/` or `public/`.
- **Plain words, simple enough for a child.** Every message says what to do next. The
  ux-critic agent (`.claude/agents/ux-critic.md`) reviews every PR that changes words a
  customer sees.
- **Fits a phone:** 360 px wide, 16 px gutters, no sideways scrolling, buttons at least
  48 px tall.
- **Prices and plan rules are owner decisions** (#432, #437, `docs/PLAN.md`). Don't change
  them in a page.
- **No secrets here.** This is a public static site; anything in `web/` is readable by
  everyone. Keys and secrets live only in the API's environment variables.

## Hosting

The site will be hosted on **Cloudflare Pages** once the owner has the account and the
domain. Until then, the CI build is enough. Settings to enter in Cloudflare Pages:

| Setting | Value |
|---|---|
| Root directory | `web` |
| Build command | `npm ci && npm run build` |
| Output directory | `dist` |
| Environment variable | `NODE_VERSION=22` |
| Production branch | `main` (previews from `development` and `beta`) |

When the domain exists, set `site` in `astro.config.mjs` to its address.

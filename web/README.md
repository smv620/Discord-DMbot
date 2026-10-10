# DMbot website (`web/`)

The customer website: static pages (home, prices, legal, how to add DMbot) and one small
signed-in area (`/account`). Built with [Astro](https://astro.build) and TypeScript in
strict mode. Built and maintained by the **WebDev** session (issue label `session: WebDev`).

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
npm run check:links  # after build: every internal link points at a real file
npm run check:csp    # after build: every inline script is allowed by the CSP
npm run preview    # serve dist/ to check the build
```

CI runs `typecheck`, `test`, `build`, `check:links` and `check:csp` in the `web` job on every
push.

### The account page and the web API

`/account` is one Preact island (`src/account/`) that talks to the web API in core
(#435). Set the API's address when building:

```bash
PUBLIC_API_BASE=https://api.example.com npm run build   # default: /api on the same site
PUBLIC_API_BASE=mock npm run dev                     # pretend API, no core needed
```

With `mock`, add `?demo=` to the address to see each state: `signed-out`, `no-plan`,
`try-it`, `table`, `grace`, `lapsed`, `down` (for example `/account?demo=grace`). The
pretend API is never included in a real build. `src/account/api.ts` is the contract with
#435: change both together.

**The API must be on the same site as the website**: `/api` on the same address, or a
subdomain of the website's domain (`api.example.com` for `example.com`). The sign-in
cookie is `SameSite=Lax`, and browsers don't send it to another site.

Payments are pages on the payment company's site (a redirect), so the CSP needs nothing
for them. An embedded checkout (Paddle.js or Lemon.js) would need its script in
`script-src` and its frame in `frame-src`.

`npm run build` also runs `scripts/csp-hashes.mjs`. It adds the hashes of Astro's small
inline island loader to `script-src` in `dist/_headers`, so the Content-Security-Policy
never needs `'unsafe-inline'` for scripts, and adds the API's address to `connect-src`
when `PUBLIC_API_BASE` is a full address. `npm run check:csp` (in CI) checks the result.
A `mock` build can't be deployed to the live site (branch `main`).

The `development` branch's build (`dev.getdmbot.com`, #837) is different: when Cloudflare
Pages builds it (`CF_PAGES_BRANCH=development`) and `PUBLIC_DEV_API_BASE` is set (a Preview
variable), that address replaces `PUBLIC_API_BASE`, is added to `connect-src`, and the
whole site gets `X-Robots-Tag: noindex, nofollow`. Other previews keep the pretend API and
`main` is never switched. See `docs/DEPLOY.md`.

### The "Say hello" page

`/hello` (#665) has two small forms, feedback and questions, in a Preact island
(`src/hello/`). They post to the web API's `POST /feedback`, which turns each message into
a GitHub Discussion (message and date only) and keeps the "how to reach you" box with the
team. Set Cloudflare Turnstile's site key when building to turn on its "are you a person?"
check; the build then allows its script and frame in the CSP:

```bash
PUBLIC_TURNSTILE_SITE_KEY=0x4AAA... npm run build   # empty: no check (local testing)
```

With `PUBLIC_API_BASE=mock` every message pretends to send.

### The admin page

`/admin` (#772) is for the team only: never linked from the site, `noindex, nofollow` in
the page and in an `X-Robots-Tag` header, and drawn only in the browser (`src/admin/`).
It signs in with Google or the admin email and password (the web API's `/admin/...`
routes) and keeps nothing in the browser but the API's HttpOnly cookie. With
`PUBLIC_API_BASE=mock` any password signs in.

### The background artwork

`public/art/table-wide.webp` (computers) and `table-tall.webp` (phones) sit behind every page
(`body::before` in `src/styles/global.css`, strength in `--art-opacity`). The owner supplied the
artwork on 2026-10-09 and confirmed it is free to use: it was made with Gemini and Claude from the
owner's own prompts. The Terms page says so, next to the "not affiliated" notice. The files
are made by `scripts/make-art.py` (half the colour, brightest parts capped, the dice lifted so they
stand out). Bare text over the art measured 4.7:1 or better on the home, prices and Q&A pages.
To change the picture, run the script on the new file and look at those pages again.
They are cached for a day (their names have no hash), so a new picture can take a day to show.

## Layout

- `src/layouts/Base.astro`: the one layout (head tags, menu, footer with legal links).
- `src/styles/global.css`: the design tokens (colors, type scale, spacing) and base styles.
  Light and dark follow the device setting. Change colors here, never in a page.
- `src/pages/`: one file per page. `/account` is the signed-in area and is kept out of
  search results.
- `test/`: Vitest smoke tests. A new page needs a line in `test/pages.test.ts` (the test fails until it has one).
- `public/_headers`: security headers for Cloudflare Pages (CSP, no framing). Add the web API address to `connect-src` when `/account` starts calling it.

Interactivity, when a page needs it, goes in a Preact island; there is no UI framework
until then.

## Rules for every page

- **No trademarks or art of the game or its publisher.** Never write "D&D", "Dungeons &
  Dragons", "Wizards of the Coast", "D&D Beyond", or the names of their books or worlds,
  and never use their logos or art. Say **"for 5e-compatible tabletop games"**. Example
  material uses our own invented names, the bake-off cast in `docs/test-scripts/`
  (Belleros, Oskar Vane, Brynwater, Gorrak, …). `npm test` fails if a banned name appears
  in `src/` or `public/`.
  **The one exception, approved by web (#469):** the not-affiliated sentence on
  `/legal/terms` ("DMbot is not affiliated with or endorsed by Wizards of the Coast…"),
  which lives in `src/content/legal.ts`. Naming the company to say we aren't connected to
  it is allowed there only; the tests check it appears on that page and nowhere else.
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

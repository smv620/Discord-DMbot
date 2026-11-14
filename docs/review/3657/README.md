# Story Graph and Names on a phone and a tablet (#3657)

One real-browser pass (Chromium, Playwright, `deviceScaleFactor: 2`, touch on) over the day's work, with every held
switch turned on locally and the pretend API, on `development` plus all of the open pull requests merged together.
13 states at 390×844 and 768×1024, in light and dark: 52 runs. The sheets next to this file show them
(`sheet-<width>-<theme>.png`, each state labelled).

What each run checked: no sideways page scroll; every button, link, tick box and slider at least 44 px to tap (a tick
box is tapped through its label); nothing wider than the window; the screenshot read by eye.

| # | What | Result |
|---|------|--------|
| 1 | Graph settings (#3620) | No sideways scroll at either width. **Fixed in #3638:** the tick rows and the sliders were 25 px tall; they are 44 px now. |
| 2 | Line panel, feeling picker and Why? (#3607) | Passed (readable, wraps, buttons 44 px, light and dark). |
| 3 | Details beside the map, See in the story graph (#3616) | Passed. On a phone the details sit under the map and the Left/Right/Under choice is hidden. |
| 4 | Not named yet: Remove, Same as someone DMbot knows…, scene (#3595, #3597, #3644) | **Fixed in #3600:** in dark mode the editor had no surface of its own, so the hint text sat on the page picture and was hard to read. It is a panel now. **Fixed here:** at 768 wide the rows were packed to 36 px for a mouse; a tablet now keeps 44 px (`pointer: fine` only). |
| 5 | The clarify page (#3604) | **Fixed in #3609:** same missing surface as 4. Otherwise passed. |
| 6 | ✅ All true / 🤫 All secret / ↩️ Undo all (#3645) | After the press: passed. The waits rule (stage, n of N, how long, last moved) is asserted by the tests (`web-waiting-bulk.test.tsx`); the pretend API answers too fast for a screenshot of the running state, so it is not pictured. |
| 7 | PC icons, all three states (#3622) | Passed: picture, 👤 and hidden render at both widths and themes; the Icon row's buttons are 44 px. |

**Found while merging the pull requests for this pass:** two stacked pull requests both append to `account.css`; a hand
merge dropped a closing brace of the secret-button hover rule, which swallowed every rule after it. It was only in my
merge, not in any pull request, but the stack should be refreshed against `development` before it merges (RepoManager's
refresh will show it). Nothing else was found.

**Not changed, and why:** the names in the map (the circles) are drawn 36 to 43 px across at 390 wide, a little under
44 px, because the whole map is fitted to the window and shrinks as names are added. Each name is also a button in the
list under the map ("See as a list"), the + and − buttons and a pinch zoom it larger, and Graph settings has a size
for the shapes. I left it as a limit of a drawn map rather than make the shapes large enough to overlap on a busy
campaign.

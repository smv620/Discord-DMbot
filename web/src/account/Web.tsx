/** @jsxImportSource preact */
// The Story Graph page (#2324): a campaign's names as a map, with the same names and connections
// as lists under it. Pick a name for the middle, choose how many steps out, tap a line to read its
// connections. A campaign's DMs can also change the words of a connection, make it secret, remove it or
// add one (the same actions as in Discord, so ↩️ Undo there still works). Who sees what is the API's
// rule: a server manager who isn't a DM gets no secrets and no editing, anyone else gets "not found".
// The page only shows what it is given. A web over BIG_WEB names asks before drawing, because a
// phone can draw it only slowly.
import { useEffect, useRef, useState } from "preact/hooks";

import { itemWords } from "../content/item-words";
import { NOT_NAMED_LIVE } from "../content/not-named-switch";
import { BIG_WEB, kindWords, webPage as words } from "../content/web-words";
import {
  type AccountApi,
  ApiError,
  type IconChoice,
  type NameChange,
  type NameRow,
  type WebGraph,
  type WebLinkChange,
  type WebOverview,
  type WebRevealTarget,
  type WebView,
} from "./api";
import { currentItem, pairFromItem } from "./itemLink";
import WebMap from "./WebMap";
import WebOverviewPanel from "./WebOverview";
import { dimmedName, markSecret, plainGraph, plainOverview } from "./know";
import { pcIconWords } from "../content/pc-icon-words";
import LazyPanel from "./LazyPanel";
import { WEB_READINGS_LIVE } from "../content/web-readings";
import { WEB_WAITING_LIVE } from "../content/web-waiting";
import { clampHops } from "./webLayout";
import { readAuto, readLook, saveAuto, saveLook, type GraphLook } from "./webPrefs";
import { listLines, type WebLine } from "./webLines";
import CampaignNav from "./CampaignNav";
import { npcPageHref } from "./webNodes";

/** The campaign's address, read from `/account/web/<server>.<campaign>`. */
export function webRefFromPath(path: string): string | null {
  const match = /^\/account\/web\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null;
  }
}

/** The name a link asked to have in the middle (`#centre=<id>`, from the Names page's "See in the story graph"), or null. */
export function centreFromHash(hash: string): string | null {
  if (!hash.startsWith("#")) return null; // (only a fragment is read, never a query string)
  const id = new URLSearchParams(hash.slice(1)).get("centre")?.trim() ?? "";
  return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null;
}

type View = "loading" | "ok" | "none" | "down" | "signed-out";

const DEFAULT_HOPS = 2;
const AUTO_MS = 5000; // how often the map reads itself again, plus up to a second more
const AUTO_JITTER_MS = 1000;
/** After this many reads in a row that found nothing new, the map waits longer between reads (an interim
 * back-off for #3294, until the campaign has a graph version that makes a full read unnecessary). */
const QUIET_LOOKS = 3;
const BACKOFF_MS = [15000, 30000, 60000]; // the waits after the quiet looks; the last one holds

/** How long to wait before the next read, not counting the jitter: 5 s while things are changing, then 15 s, 30 s
 * and at most 60 s as reads keep finding the same graph. Any change, the tab coming back or the button resets it. */
export function autoDelay(unchanged: number): number {
  if (unchanged < QUIET_LOOKS) return AUTO_MS;
  return BACKOFF_MS[Math.min(unchanged - QUIET_LOOKS, BACKOFF_MS.length - 1)] ?? AUTO_MS;
}

/** Two reads that are the same text draw the same map, so the second changes nothing on the page. The whole
 * read is the signature: a secret turned on, a know word, a pin or a belief all show without a reload. */
const graphSignature = (g: WebGraph): string => JSON.stringify(g);

/** A quiet read that hangs is dropped after this long, so one stuck request cannot stop the next turns. */
const QUIET_READ_MS = 20000;

function withTimeout<T>(work: Promise<T>): Promise<T> {
  return Promise.race([
    work,
    new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), QUIET_READ_MS)),
  ]);
}

const UNNAMED_SUFFIX = "(name unknown)"; // core/memory/unnamed.py: how a person the story never named is labelled

function refusal(error: unknown): string | null {
  const kind = error instanceof ApiError ? error.kind : "network";
  if (kind === "signed-out") return null;
  return words.errors[kind] ?? words.errorFallback;
}

export default function Web({ api, path }: { api: AccountApi; path: string }) {
  const ref = webRefFromPath(path);
  const [view, setView] = useState<View>("loading");
  const [graph, setGraph] = useState<WebGraph | null>(null);
  const [center, setCenter] = useState<string | null>(null);
  const [hops, setHops] = useState(DEFAULT_HOPS);
  // What is typed in the steps box: it may be half-typed, so it is kept apart from the steps in use.
  const [hopsText, setHopsText] = useState(String(DEFAULT_HOPS));
  const [editing, setEditing] = useState<string | null>(null);
  // The list at the bottom is closed until asked for, and each name in it opens on its own (or all together).
  const [listOpen, setListOpen] = useState(false);
  // How this viewer likes the map drawn (#3620): kept in this browser, never sent anywhere.
  const [look, setLookState] = useState<GraphLook>(readLook);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The last icon change, so one Undo can post the old choice back (#3622).
  const [iconBack, setIconBack] = useState<{ id: string; previous: IconChoice; now: IconChoice } | null>(null);
  const setLook = (next: GraphLook): void => {
    setLookState(next);
    saveLook(next);
  };
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  // A web too big to draw without asking: what was asked for, held until the person says yes.
  const [big, setBig] = useState<{ names: number; center: string | null; hops: number } | null>(null);
  const [line, setLine] = useState<{ a: string; b: string } | null>(null);
  // A private message's "Open on the website" can name one line, as `?item=<idA>.<idB>` (#3663): its panel is opened once the
  // graph is here, or the page says it has been answered (or is not drawn any more).
  const wantedLine = useRef(((item) => (item === null ? null : pairFromItem(item)))(currentItem()));
  const [lineGone, setLineGone] = useState(false);
  const lineTries = useRef(0);
  const [news, setNews] = useState<{ ok: boolean; text: string; n: number } | null>(null);
  const latest = useRef(0);
  const sayings = useRef(0);
  // Which web is asked for (#2405): a DM's "players" or "full"; a player who peeks, "full"; else the
  // API's own pick. Kept in a ref too, so a read started before a switch is not mixed up with it.
  const [asked, setAsked] = useState<WebView | undefined>(undefined);
  const askedNow = useRef<WebView | undefined>(undefined);
  // The character the web is read as (#2999), or "party"; a ref, like askedNow, so a read in flight uses it.
  const seeAsNow = useRef<string | undefined>(undefined);
  const [seeAsNote, setSeeAsNote] = useState("");
  // Once a response says the person is a DM they stay one for this visit, even in the players' view.
  const [isDm, setIsDm] = useState(false);
  const [meta, setMeta] = useState<{ visibility?: WebGraph["visibility"] | undefined; canPeek?: boolean | undefined }>({});
  const [asking, setAsking] = useState(false);
  // The overview (#2632): every name, those with no connection yet and the groups. Missing from an older API
  // or while it loads: then the page is the map alone, as before.
  const [overview, setOverview] = useState<WebOverview | null>(null);
  const [alone, setAlone] = useState<string | null>(null);
  const overviewLatest = useRef(0);
  const peekSwitch = useRef<HTMLInputElement>(null);
  // The map reads itself again every few seconds unless the person froze it; kept in this browser.
  const [auto, setAuto] = useState(readAuto);
  const graphNow = useRef<WebGraph | null>(null);
  graphNow.current = graph;
  const autoNow = useRef(auto);
  autoNow.current = auto;
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);
  const refreshNow = useRef<() => Promise<"changed" | "same" | "skipped">>(async () => "skipped");
  // True while a name is being dragged: a read then would redraw the map under the person's hand (#3324).
  const dragging = useRef(false);
  // Reads in a row that found the same graph; it sets the wait before the next read (autoDelay).
  const unchanged = useRef(0);
  // The graph's version number and the view it was read for, as of the last full read (#3294): while both are the same
  // the story has not changed, so the turn makes no full read. Null until a full read has been made, or after one the
  // person asked for.
  const seenVersion = useRef<{ key: string; version: number } | null>(null);
  // Bumped when the person reads a web themselves, so a long wait already running starts again at the quick pace.
  const [pulse, setPulse] = useState(0);

  /** Reads the web around `at`. A big one waits for a yes unless `sure`. */
  async function load(at: string | null, steps: number, sure = false): Promise<void> {
    if (!ref || !api.webGraph) return;
    const mine = ++latest.current;
    unchanged.current = 0;
    seenVersion.current = null;
    setPulse((n) => n + 1);
    const view = askedNow.current;
    setBig(null);
    let readingSeeAs = false; // only a failed read of the character's web means the character is gone (#3161)
    try {
      // The size is of the DM's own web, so it is not asked while the web is read as someone else.
      if (!sure && api.webSize && seeAsNow.current === undefined) {
        const size = await api.webSize(ref, { center: at, hops: steps, ...(view ? { view } : {}) });
        if (mine !== latest.current) return;
        if (size.names > BIG_WEB) {
          setBig({ names: size.names, center: at, hops: steps });
          return;
        }
      }
      readingSeeAs = seeAsNow.current !== undefined;
      // The version number from before the read, so the first quiet turn after it has something to compare (#3294).
      let version: number | null = null;
      if (api.webVersion) {
        try {
          version = (await api.webVersion(ref)).version;
        } catch {
          version = null; // (the turns then read the whole graph, as before)
        }
      }
      const read = await api.webGraph(ref, {
        center: at,
        hops: steps,
        ...(view ? { view } : {}),
        ...(seeAsNow.current !== undefined ? { seeAs: seeAsNow.current } : {}),
      });
      if (mine !== latest.current) return;
      if (version !== null) {
        // (the same query a quiet turn makes: the centre and steps as the page now holds them)
        const key = JSON.stringify({
          center: read.center,
          hops: clampHops(steps, read.maxHops),
          ...(view ? { view } : {}),
          ...(seeAsNow.current !== undefined ? { seeAs: seeAsNow.current } : {}),
        });
        seenVersion.current = { key, version };
      }
      setGraph(read.canEdit ? read : plainGraph(read));
      if (read.canEdit) setIsDm(true);
      setMeta((now) => ({
        visibility: read.visibility ?? now.visibility,
        canPeek: read.canPeek ?? now.canPeek,
      }));
      setCenter(read.center);
      setHops(clampHops(steps, read.maxHops));
      setHopsText(String(clampHops(steps, read.maxHops)));
      setLine((now) => (now && read.edges.some((e) => (e.a === now.a && e.b === now.b) || (e.a === now.b && e.b === now.a)) ? now : null));
      setView("ok");
    } catch (error) {
      if (mine !== latest.current) return;
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "not-found" && readingSeeAs) {
        // The character is no longer in the story: say so, and read the DM's Players View again.
        seeAsNow.current = undefined;
        setSeeAsNote(words.seeAsGone);
        void load(center, hops, true);
        return;
      }
      if (kind === "peek-off") {
        // The DM turned peeking off while it was on: back to the normal view, with the reason.
        switchTo(undefined);
        setOverview(null);
        void loadOverview();
        setMeta((now) => ({ ...now, visibility: "private", canPeek: false }));
        setNews({ ok: false, text: words.errors["peek-off"]!, n: ++sayings.current });
        await load(at, steps, true);
        return;
      }
      if (kind === "signed-out") setView("signed-out");
      else if (kind === "not-found" || kind === "not-allowed") setView("none");
      else setView("down");
    }
  }

  /** One quiet read of the web as it is shown now: nothing moves unless the story changed. It never draws a
   * web that needs the big-web yes (it skips those, and asks only the cheap size first), gives way to any read
   * the person started meanwhile, and drops its answer if they froze the graph or left. A person who is signed
   * out, or whose peeking was turned off, is told as a normal read would tell them; any other failure waits
   * for the next turn. */
  async function refresh(): Promise<"changed" | "same" | "skipped"> {
    if (dragging.current) return "skipped";
    if (!ref || !api.webGraph || view !== "ok" || big !== null || revealing.size > 0) return "skipped";
    const shown = graphNow.current;
    if (!shown) return "skipped";
    const stamp = latest.current;
    const [at, steps] = [center, hops];
    const query = {
      center: at,
      hops: steps,
      ...(askedNow.current ? { view: askedNow.current } : {}),
      ...(seeAsNow.current !== undefined ? { seeAs: seeAsNow.current } : {}),
    };
    // A read that was on its way when a drag began is dropped too, so the map is not redrawn under the hand (#3324).
    const stale = (): boolean =>
      !alive.current || !autoNow.current || stamp !== latest.current || graphNow.current !== shown || dragging.current;
    // The cheap read first (#3294): a number that goes up with every change to the story. The same number for the same
    // view means nothing to read. Any trouble with it just means a full read this turn, as before it existed.
    const viewKey = JSON.stringify(query);
    let version: number | null = null;
    if (api.webVersion) {
      try {
        version = (await withTimeout(api.webVersion(ref))).version;
      } catch {
        version = null;
      }
      if (stale()) return "skipped";
      const seen = seenVersion.current;
      if (version !== null && seen && seen.key === viewKey && seen.version === version) return "same";
    }
    try {
      if (api.webSize && seeAsNow.current === undefined) {
        const size = await withTimeout(api.webSize(ref, { center: at, hops: steps, ...(askedNow.current ? { view: askedNow.current } : {}) }));
        if (stale() || size.names > BIG_WEB) return "skipped";
      }
      const read = await withTimeout(api.webGraph(ref, query));
      if (stale()) return "skipped";
      // (the number from before the read, so a change made while it was read shows at the next turn)
      seenVersion.current = version === null ? null : { key: viewKey, version };
      const next = read.canEdit ? read : plainGraph(read);
      if (graphSignature(next) === graphSignature(shown)) return "same";
      setGraph(next);
      setLine((now) => (now && next.edges.some((e) => (e.a === now.a && e.b === now.b) || (e.a === now.b && e.b === now.a)) ? now : null));
      void loadOverview();
      return "changed";
    } catch (error) {
      if (stale()) return "skipped";
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "signed-out") setView("signed-out");
      else if (kind === "peek-off") void load(at, steps, true);
      /* anything else is simply tried again at the next turn */
      return "skipped";
    }
  }
  refreshNow.current = refresh;

  // Every 5 seconds, give or take up to a second so open tabs drift apart; only while the tab is on screen, and
  // at once when it comes back.
  useEffect(() => {
    if (!auto || view !== "ok") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    const next = (): void => {
      timer = setTimeout(async () => {
        if (!document.hidden) {
          const found = await refreshNow.current();
          // Only a read that really compared two graphs counts: a skipped turn says nothing about the story.
          if (found === "changed") unchanged.current = 0;
          else if (found === "same") unchanged.current += 1;
        }
        if (!stopped) next();
      }, autoDelay(unchanged.current) + Math.random() * AUTO_JITTER_MS);
    };
    const back = (): void => {
      if (document.hidden) return;
      unchanged.current = 0; // the tab is back: look at once, and keep to the quick pace again
      setPulse((n) => n + 1); // (the timer that was waiting out a long delay starts again at the quick one)
      void refreshNow.current();
    };
    document.addEventListener("visibilitychange", back);
    next();
    return () => {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener("visibilitychange", back);
    };
  }, [auto, view, pulse]);

  function toggleAuto(): void {
    const on = !auto;
    setAuto(on);
    saveAuto(on);
    unchanged.current = 0;
    if (on) void refresh();
  }

  /** Changes which web is asked for, and reads it (#2405). */
  function switchTo(next: WebView | undefined): void {
    askedNow.current = next;
    setAsked(next);
  }
  function chooseView(next: WebView | undefined): void {
    seeAsNow.current = undefined; // Full View, or the Players View, is never "as someone" (#2999)
    setSeeAsNote("");
    switchTo(next);
    setLine(null);
    setAlone(null);
    setOverview(null); // never leave the other view's names on screen while this one loads
    void load(center, hops, true);
    void loadOverview();
  }

  /** Reads the web as one character, or the party, knows it (#2999); undefined goes back to the Players View. */
  function chooseSeeAs(next: string | undefined): void {
    seeAsNow.current = next;
    setSeeAsNote("");
    setLine(null);
    setAlone(null);
    void load(center, hops, true);
  }

  /** A player agreed to the warning: tell the API they peek, then read the full web. */
  async function peek(): Promise<void> {
    setAsking(false);
    if (!ref || !api.webPeek) return;
    try {
      await api.webPeek(ref);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return setView("signed-out");
      // Only the API saying the DM turned peeking off may lock the switch; a network or server
      // failure says so in its own words and leaves the switch usable to try again.
      if (error instanceof ApiError && error.kind === "peek-off") {
        setMeta((now) => ({ ...now, visibility: "private", canPeek: false }));
      }
      setNews({ ok: false, text: refusal(error) ?? words.errorFallback, n: ++sayings.current });
      return;
    }
    chooseView("full");
    peekSwitch.current?.focus();
  }

  const [revealing, setRevealing] = useState<Set<string>>(new Set());

  /** A DM shows a name or a connection to the players, then the web is read again. */
  async function reveal(target: WebRevealTarget): Promise<string | null> {
    if (!ref || !api.webReveal) return words.errorFallback;
    const key = `${target.kind}:${target.id}`;
    if (revealing.has(key)) return null; // it can take up to a minute: one press at a time
    setRevealing((now) => new Set(now).add(key));
    try {
      await api.webReveal(ref, target);
    } catch (error) {
      if (target.kind === "link" && error instanceof ApiError && (error.kind === "names-not-shown" || error.kind === "name-unnamed")) {
        // Which end needs what: the page already holds the two names, so it can say them (#3426).
        const ends = graph?.edges.find((e) => e.links.some((l) => l.id === target.id));
        const ones = ends ? graph!.nodes.filter((n) => n.id === ends.a || n.id === ends.b) : [];
        if (error.kind === "name-unnamed") return words.nameThemFirst(ones.find((n) => n.name.endsWith(UNNAMED_SUFFIX))?.name.slice(0, -UNNAMED_SUFFIX.length).trim() ?? null, NOT_NAMED_LIVE && api.nameThem !== undefined);
        return words.showNamesFirst(ones.filter((n) => n.revealed === false).map((n) => n.name));
      }
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      return said;
    } finally {
      setRevealing((now) => {
        const next = new Set(now);
        next.delete(key);
        return next;
      });
    }
    setNews({ ok: true, text: words.revealed, n: ++sayings.current });
    await load(center, hops, true);
    void loadOverview(); // (a new connection or a changed name moves names in and out of the tray)
    return null;
  }

  /** Reads the overview in the view being asked for; a failure leaves the page as the map alone. */
  async function loadOverview(): Promise<void> {
    if (!ref || !api.webOverview) return;
    const mine = ++overviewLatest.current;
    const view = askedNow.current;
    try {
      const got = await api.webOverview(ref, view ? { view } : {});
      if (mine === overviewLatest.current) setOverview(got.canEdit ? got : plainOverview(got));
    } catch {
      if (mine === overviewLatest.current) setOverview(null);
    }
  }

  useEffect(() => {
    const want = wantedLine.current;
    if (want === null || graph === null) return;
    const [a, b] = want;
    if (graph.edges.some((e) => (e.a === a && e.b === b) || (e.a === b && e.b === a))) {
      wantedLine.current = null;
      setLine({ a, b });
    } else if (lineTries.current === 0) {
      // Not drawn around the middle name now: ask for the graph around one end of it, once, before saying it is gone.
      lineTries.current = 1;
      void load(a, hops);
    } else {
      wantedLine.current = null;
      setLineGone(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  useEffect(() => {
    setOverview(null); // (another campaign's names are never shown under this one)
    setAlone(null);
    seeAsNow.current = undefined; // and a campaign never inherits the character it was read as (#3161)
    setSeeAsNote("");
    void load(centreFromHash(typeof window === "undefined" ? "" : window.location.hash), DEFAULT_HOPS);
    void loadOverview();
    // Only the campaign restarts the first read; later reads are the person's choices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref]);

  /** A player character's icon (#3622): instant, then the web is read again so the circle shows it. Returns a plain-words problem. */
  async function chooseIcon(id: string, choice: IconChoice): Promise<string | null> {
    if (!ref || !api.setPcIcon) return words.errorFallback;
    let got: { previous: IconChoice; now: IconChoice };
    try {
      got = await api.setPcIcon(ref, id, choice);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      if (kind === "unavailable" || kind === "not-found") void load(center, hops, true); // (the picture or character went away: show what is left)
      return pcIconWords.problems[kind] ?? pcIconWords.problems.server ?? words.errorFallback;
    }
    // Undo only while the icon is still what this change made it: a change by someone else since must not be undone over.
    setIconBack(got.previous === got.now ? null : { id, previous: got.previous, now: got.now });
    void load(center, hops, true).catch(() => undefined); // (the change is saved: a failed re-read is not a failed save)
    return null;
  }

  /** One change to the connections; the web is read again after it so the lines are the API's own. */
  /** `own` words one kind of refusal for this one call (the store's rule saying no to a companion, #3505); every other
   * failure, a network failure, a busy server, being signed out, is worded as it always is. */
  async function act(change: WebLinkChange, ok: string, own?: Partial<Record<string, string>>): Promise<string | null> {
    if (!ref || !api.changeWebLink) return words.errorFallback;
    try {
      await api.changeWebLink(ref, change);
    } catch (error) {
      const said = (error instanceof ApiError && own?.[error.kind]) || refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      if (error instanceof ApiError && error.kind === "not-found") {
        setNews({ ok: false, text: said, n: ++sayings.current });
        await load(center, hops, true);
        return null;
      }
      return said;
    }
    setNews({ ok: true, text: ok, n: ++sayings.current });
    await load(center, hops, true);
    void loadOverview(); // (a new connection or a changed name moves names in and out of the tray)
    return null;
  }

  /** One change to a name (spelling or kind): the same route the Names page uses, then the web is read again. */
  async function actName(id: string, change: NameChange, ok: string): Promise<string | null> {
    if (!ref || !api.changeName) return words.errorFallback;
    try {
      await api.changeName(ref, id, change);
    } catch (error) {
      const said = refusal(error);
      if (said === null) {
        setView("signed-out");
        return "";
      }
      return said;
    }
    setNews({ ok: true, text: ok, n: ++sayings.current });
    await load(center, hops, true);
    void loadOverview(); // (a new connection or a changed name moves names in and out of the tray)
    return null;
  }

  if (!ref || !api.webGraph) return <p role="status">{words.notYours}</p>;
  if (view === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{words.signedOut}</p>
      </div>
    );
  if (view === "none") return <p role="status">{words.notYours}</p>;
  if (view === "down")
    return (
      <div class="stack">
        <p class="note bad" role="alert">
          {words.down}
        </p>
        <button type="button" class="button secondary" onClick={() => void load(center, hops)}>
          {words.tryAgain}
        </button>
      </div>
    );
  const askBox = big !== null && (
        <div class="panel stack" role="alert">
          <p>{words.bigAsk(big.names)}</p>
          <div class="row">
            <button type="button" class="button" onClick={() => void load(big.center, big.hops, true)}>
              {words.bigYes}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                // Declining the first draw must not leave a blank page: read the nearest names only.
                if (!graph) void load(big.center, 1, true);
                else setBig(null);
              }}
            >
              {words.bigNo}
            </button>
          </div>
        </div>
      );

  if (view === "loading" && big !== null)
    return <div class="stack web-page">{askBox}</div>;
  if (view === "loading")
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  if (!graph)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  // The API never sends a secret to someone it isn't for; this keeps one from showing if it did.
  // Secrets are for a DM in Full View and for a player who is peeking, never in the players' view.
  const showAll = asked === "players" ? false : graph.canEdit || asked === "full";
  // Changing anything is for a DM, and not in the players' view (read-only, so what they see is checked).
  const canEdit = graph.canEdit && asked !== "players";
  // Set while the web is read as one character (#2999): read-only, like the Players View.
  const seeing = graph.seeAs ?? null;
  const peeking = !isDm && asked === "full";
  // Nor what the players haven't been told yet: the page may still hold the full web for a moment
  // after a switch to the players' view, and this keeps it off the screen.
  // A kind the viewer left unticked in the graph settings drops its names, and so their lines; the middle name always stays.
  // (A kind saved from another campaign, that this one has no names of, is ignored: it would otherwise hide everything.)
  const activeKinds = look.kinds.filter((k) => graph.nodes.some((n) => n.kind === k));
  const kindShown = (n: { id: string; kind: string }): boolean => activeKinds.length === 0 || activeKinds.includes(n.kind) || n.id === center;
  const nodes = graph.nodes.filter((n) => (showAll || (!n.secret && n.revealed !== false)) && kindShown(n));
  const visible = new Set(nodes.map((n) => n.id));
  const edges = graph.edges
    .filter((e) => visible.has(e.a) && visible.has(e.b))
    // For anyone but a DM the label is rebuilt from the known connections, so a secret one's words can
    // never ride along on the line even if the API sent them.
    .map((e) => {
      // A pin and DMbot's check are the DM's alone: dropped for a player who peeks, and in the players' view.
      if (showAll && canEdit) return e;
      if (showAll) return { ...e, links: e.links.map(({ pinned: _pinned, check: _check, quote: _quote, ...l }) => l) };
      const links = e.links
        .filter((l) => !l.secret && l.revealed !== false)
        .map(({ belief, pinned: _pinned, check: _check, quote: _quote, ...l }) => (belief ? { ...l, belief: { confidence: belief.confidence, status: belief.status } } : l));
      return { ...e, links, label: links.at(-1)?.words ?? "", more: Math.max(0, links.length - 1) };
    })
    .filter((e) => e.links.length > 0);
  // The kinds the campaign has, with how many names of each, for the settings' tick boxes (counted before the filter).
  const kindCounts = [...new Set(graph.nodes.filter((n) => showAll || (!n.secret && n.revealed !== false)).map((n) => n.kind))].map((key) => ({
    key,
    label: kindWords(key),
    count: graph.nodes.filter((n) => n.kind === key && (showAll || (!n.secret && n.revealed !== false))).length,
  }));
  const nameOf = (id: string): string => nodes.find((n) => n.id === id)?.name ?? id;
  const kindOf = (id: string): string => nodes.find((n) => n.id === id)?.kind ?? "";
  const { own, further } = listLines(edges, center, nameOf);
  const maxHops = graph.maxHops;
  // The − and + beside the steps box: one step more or fewer, kept between 1 and the most the API allows.
  const stepHops = (by: number): void => {
    const next = Math.min(maxHops, Math.max(1, hops + by));
    if (next === hops) return;
    setHopsText(String(next));
    void load(center, next);
  };
  const chosen = line ? (edges.find((e) => e.a === line.a && e.b === line.b || e.a === line.b && e.b === line.a) ?? null) : null;

  return (
    <div class="stack web-page">
      {ref && <CampaignNav api={api} campaignRef={ref} page="graph" />}
      {isDm && (
        <fieldset class="web-view">
          <legend>{words.viewLegend}</legend>
          <label class="choice">
            <input type="radio" name="web-view" checked={asked !== "players"} onChange={() => chooseView("full")} />
            <span>{words.fullView}</span>
          </label>
          <label class="choice">
            <input type="radio" name="web-view" checked={asked === "players"} onChange={() => chooseView("players")} />
            <span>{words.playersView}</span>
          </label>
          <p class="small muted">{asked === "players" ? words.playersViewNote : words.fullViewNote}</p>
          {asked === "players" && api.webViewers && (
            <SeeAsPicker api={api} refId={ref} seeing={seeing} note={seeAsNote} onPick={(id) => chooseSeeAs(id)} onBack={() => chooseSeeAs(undefined)} />
          )}
        </fieldset>
      )}
      {!isDm && api.webPeek && (
        <PeekSwitch
          on={asked === "full"}
          disabled={meta.canPeek === false || meta.visibility === "private"}
          inputRef={peekSwitch}
          onTurnOn={() => setAsking(true)}
          onTurnOff={() => chooseView(undefined)}
        />
      )}
      {asking && (
        <PeekAsk
          onYes={() => void peek()}
          onCancel={() => {
            setAsking(false);
            peekSwitch.current?.focus();
          }}
        />
      )}
      {peeking && <p class="note" role="note">{words.peeking}</p>}

      <div class="web-tools-form">
        {/* Hidden while the web is read as someone: the search reads the DM's own names, not theirs (#3161). */}
        {!seeing && <CentrePicker api={api} refId={ref} current={center ? nameOf(center) : ""} onPick={(id) => void load(id, hops)} asPlayers={!canEdit} view={asked} />}
        <div class="field">
          <label for="web-hops">{words.hopsLabel}</label>
          <div class="row web-spinner">
            <button
              type="button"
              class="button secondary"
              aria-label={words.hopsLess}
              disabled={hops <= 1}
              onClick={() => stepHops(-1)}
            >
              −
            </button>
          <input
            id="web-hops"
            type="number"
            inputMode="numeric"
            min={1}
            max={maxHops}
            value={hopsText}
            aria-describedby="web-hops-hint"
            onInput={(e) => {
              const text = (e.target as HTMLInputElement).value;
              setHopsText(text);
              const n = Number(text);
              if (text.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= maxHops) void load(center, n);
            }}
            onBlur={() => setHopsText(String(hops))}
          />
            <button
              type="button"
              class="button secondary"
              aria-label={words.hopsMore}
              disabled={hops >= maxHops}
              onClick={() => stepHops(1)}
            >
              +
            </button>
          </div>
        </div>
        <p id="web-hops-hint" class="small muted">
          {words.hopsHint(maxHops)}
        </p>
        <div class="field">
          <button type="button" class="button secondary" onClick={toggleAuto}>
            {auto ? words.freezeGraph : words.autoUpdateGraph}
          </button>
          <p class="small muted" role="status" aria-live="polite">
            {auto ? words.updatingNote : words.frozenNote}
          </p>
        </div>
      </div>

      {graph.truncated && <p class="note">{words.truncated}</p>}
      {lineGone && (
        <p class="note" role="status">
          {itemWords.answered}
        </p>
      )}
      <p class={news?.ok ? "ok" : "visually-hidden"} role="status">
        {news?.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>
      <p class={news && !news.ok ? "warn" : "visually-hidden"} role="alert">
        {news && !news.ok ? <span key={news.n}>{news.text}</span> : ""}
      </p>

      <p class="web-count" role="status" aria-live="polite">
        <strong>{words.howMany(nodes.length, edges.reduce((n, e) => n + e.links.length, 0))}</strong>
      </p>

      {nodes.length <= 1 && edges.length === 0 ? (
        <p class="muted">{words.empty}</p>
      ) : (
        center && (
          <>
            <a class="small" href="#web-list" onClick={() => setListOpen(true)}>
              {words.skipMap}
            </a>
            <div class="row">
              <button type="button" class="button secondary" aria-expanded={settingsOpen} aria-controls="graph-settings" onClick={() => setSettingsOpen((o) => !o)}>
                {words.settingsButton}
              </button>
            </div>
            {settingsOpen && (
              <div id="graph-settings">
                <LazyPanel
                  load={() => import("./WebSettings")}
                  what={words.settingsLoading}
                  props={{ look, kinds: kindCounts, onChange: setLook }}
                />
              </div>
            )}
            <WebMap
              key={ref}
              api={api}
              onDragging={(on) => {
                dragging.current = on;
              }}
              nodes={nodes}
              edges={edges}
              center={center}
              refId={ref}
              selected={line}
              onCenter={(id) => void load(id, hops)}
              onLine={(a, b) => setLine({ a, b })}
              onNodePick={() => setLine(null)}
              look={look}
              onIcon={seeing ? undefined : chooseIcon}
              iconUndo={iconBack ? { id: iconBack.id, now: iconBack.now, undo: async () => {
                const back = iconBack;
                const refused = await chooseIcon(back.id, back.previous);
                if (!refused) setIconBack(null);
                return refused;
              } } : null}
              canEdit={canEdit}
            >
              {chosen && (
                <LazyPanel
                  key={`${chosen.a}|${chosen.b}`}
                  load={() => import("./WebLinePanel")}
                  what="the connections"
                  props={{ edge: chosen, nameOf, kindOf, canEdit, act, reveal, revealing, api, refId: ref, onClose: () => setLine(null) }}
                />
              )}
            </WebMap>
          </>
        )
      )}

      <p class="muted small">{words.lead}</p>
      {canEdit ? (
        <p class="small muted">{words.undoNote}</p>
      ) : (
        <p class="note" role="note">
          {words.readOnly}
        </p>
      )}

      {askBox}

      {overview && !seeing && (
        <WebOverviewPanel
          overview={overview}
          refId={ref}
          center={center}
          alone={alone}
          onCentre={(id) => void load(id, Math.max(hops, graph?.maxHops ?? hops))}
          onAlone={setAlone}
        />
      )}

      {/* (both lists load on demand, and the switch is tested here so the build drops a list that is not live: the page stays within its weight budget) */}
      {WEB_READINGS_LIVE && overview?.canEdit && asked !== "players" && (
        <LazyPanel key={`readings-${ref}`} load={() => import("./WebReadings")} what="what people believe" props={{ api, campaignRef: ref }} />
      )}

      {WEB_WAITING_LIVE && overview?.canEdit && asked !== "players" && (
        <LazyPanel key={ref} load={() => import("./WebWaiting")} what="the waiting connections" props={{ api, campaignRef: ref, onChanged: () => void load(center, hops, true) }} />
      )}

      <details
        class="web-list-box"
        id="web-list"
        open={listOpen}
        onToggle={(e) => setListOpen((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary>{words.listHeading}</summary>
        <div class="stack">
        <div class="row web-list-tools">
          <h3 class="small">{words.namesHeading}</h3>
          <button type="button" class="button secondary" onClick={() => setOpenRows(new Set(nodes.map((n) => n.id)))}>
            {words.expandAll}
          </button>
          <button type="button" class="button secondary" onClick={() => setOpenRows(new Set())}>
            {words.collapseAll}
          </button>
        </div>
        {(nodes.some((n) => n.knowWord) || graph.edges.some((e) => e.links.some((l) => l.knowWord))) && (
          <p class="small muted">{words.knowHint}</p>
        )}
        <ul class="web-names">
          {nodes.map((n) => {
            const open = openRows.has(n.id);
            return (
              <li key={n.id} class={`web-name${dimmedName(n) ? " web-dim" : ""}`}>
                <button
                  type="button"
                  class="web-name-toggle"
                  aria-expanded={open}
                  aria-controls={`web-name-${n.id}`}
                  onClick={() =>
                    setOpenRows((was) => {
                      const next = new Set(was);
                      if (!next.delete(n.id)) next.add(n.id);
                      return next;
                    })
                  }
                >
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span>
                  <span class="campaign-name">
                    {markSecret(n) && <span aria-hidden="true">{words.secretMark} </span>}
                    {n.name}
                  </span>
                  {!n.knowWord && n.secret && <span class="visually-hidden"> ({words.secretWords})</span>}
                  {!n.knowWord && n.revealed === false && <span class="small muted"> ({words.notRevealed})</span>}
                  {n.knowWord && words.knowWords[n.knowWord] && <span class="small muted"> ({words.knowWords[n.knowWord]})</span>}
                  <span class="tag">{kindWords(n.kind)}</span>
                  {n.id === center && <span class="small muted">{words.inMiddle}</span>}
                </button>
                {open && (
                  <div class="web-name-more row" id={`web-name-${n.id}`}>
                    {n.page === true && npcPageHref(ref, n.id) && (
                      <a class="small" href={npcPageHref(ref, n.id) ?? undefined}>
                        {words.openItsPage}
                        <span class="visually-hidden">{words.openItsPageFor(n.name)}</span>
                      </a>
                    )}
                    {n.id !== center && (
                      <button type="button" class="button secondary" onClick={() => void load(n.id, hops)} aria-label={words.makeMiddle(n.name)}>
                        {words.makeMiddleShort}
                      </button>
                    )}
                    {canEdit && n.revealed === false && api.webReveal && (
                      <button
                        type="button"
                        class="button secondary"
                        aria-label={words.revealFor(n.name)}
                        disabled={revealing.has(`name:${n.id}`)}
                        onClick={() =>
                          void reveal({ kind: "name", id: n.id }).then((said) => {
                            // A refusal or a give-up is said in its own words, never swallowed.
                            if (said) setNews({ ok: false, text: said, n: ++sayings.current });
                          })
                        }
                      >
                        {words.reveal}
                      </button>
                    )}
                    {canEdit && api.changeName && (
                      <button
                        type="button"
                        class="button secondary"
                        aria-expanded={editing === n.id}
                        aria-label={words.changeFor(n.name)}
                        onClick={() => setEditing(editing === n.id ? null : n.id)}
                      >
                        {editing === n.id ? words.doneShort : words.changeShort}
                      </button>
                    )}
                    {canEdit && editing === n.id && <LazyPanel key={n.id} load={() => import("./WebNodePanel")} what="the name editor" props={{ node: n, act: actName, api, refId: ref }} />}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <h3 class="small">{words.linesHeading}</h3>
        <WebLines lines={own} onOpen={(a, b) => setLine({ a, b })} />
        {further.length > 0 && (
          <>
            <h3 class="small">{words.furtherOut}</h3>
            <p class="small muted">{words.furtherOutHint(center ? nameOf(center) : "")}</p>
            <WebLines lines={further} onOpen={(a, b) => setLine({ a, b })} />
          </>
        )}
        </div>
      </details>
    </div>
  );
}

/** Connections, one line each, read subject, what it does, object (#2666). The arrows are for the
 * eye; a screen reader gets the plain sentence. */
function WebLines({ lines, onOpen }: { lines: WebLine[]; onOpen: (a: string, b: string) => void }) {
  return (
    <ul class="web-lines">
      {lines.map((l) => (
        <li key={l.key} class="name-row">
          {markSecret(l.link) && <span aria-hidden="true">{words.secretMark} </span>}
          {l.link.belief && <span aria-hidden="true">{words.theoryMark} </span>}
          <span aria-hidden="true">
            {l.after ? `${l.from} ${l.words} ${l.to} ${l.after}` : `${l.from} → ${l.words} → ${l.to}`}
          </span>
          {l.link.knowWord && words.knowWords[l.link.knowWord] && <span class="small muted"> ({words.knowWords[l.link.knowWord]})</span>}
          <span class="visually-hidden">
            {words.lineText(l.from, l.words, l.to, l.after)}
            {!l.link.knowWord && l.link.secret ? ` (${words.secretWords})` : ""}
            {l.link.belief ? ` (${words.theoryWords})` : ""}
          </span>{" "}
          <button
            type="button"
            class="button secondary"
            onClick={() => onOpen(l.edge.a, l.edge.b)}
            aria-label={words.seeLine(l.from, l.to)}
          >
            {words.seeLineShort}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** "See as…" (#2999): a DM looks at the web as one character, or the party, knows it. Shown in the Players View only. */
function SeeAsPicker({
  api,
  refId,
  seeing,
  note,
  onPick,
  onBack,
}: {
  api: AccountApi;
  refId: string;
  seeing: { viewer: string; name: string } | null;
  note: string;
  onPick: (id: string) => void;
  onBack: () => void;
}) {
  const [q, setQ] = useState("");
  const [list, setList] = useState<{ id: string; name: string }[]>([]);
  const [total, setTotal] = useState(0);
  const [failed, setFailed] = useState(false);
  const latest = useRef(0);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!api.webViewers) return;
    const mine = ++latest.current;
    // The first look is at once; after that the list waits for the last key (300 ms).
    const timer = setTimeout(() => {
      api
        .webViewers!(refId, q.trim())
        .then((got) => {
          if (mine !== latest.current) return;
          setList(got.viewers);
          setTotal(got.total);
          setFailed(false);
        })
        .catch(() => {
          // A failed look leaves no results for an older question beside the error (#3161).
          if (mine !== latest.current) return;
          setList([]);
          setTotal(0);
          setFailed(true);
        });
    }, q === "" ? 0 : 300);
    return () => clearTimeout(timer);
  }, [api, refId, q]);

  return (
    <div class="field">
      <label>
        {words.seeAsLabel}
        <input
          type="search"
          value={q}
          placeholder={words.seeAsPlaceholder}
          autoComplete="off"
          spellcheck={false}
          aria-describedby="web-see-as-hint"
          ref={box}
          onInput={(e) => setQ((e.target as HTMLInputElement).value)}
        />
      </label>
      <p id="web-see-as-hint" class="small muted">
        {words.seeAsHint}
      </p>
      {seeing && <p role="status">{words.seeAsNow(seeing.name)}</p>}
      {seeing && (
        <p>
          <button
            type="button"
            class="button secondary"
            onClick={() => {
              onBack();
              box.current?.focus(); // the button goes away once the character clears, so focus goes to the box
            }}
          >
            {words.seeAsBack}
          </button>
        </p>
      )}
      {note && <p role="status">{note}</p>}
      <ul class="web-found" aria-label={words.seeAsListLabel}>
        <li>
          <button
            type="button"
            class="button secondary"
            aria-current={seeing?.viewer === "party" ? "true" : undefined}
            onClick={() => onPick("party")}
          >
            {words.seeAsParty}
          </button>
        </li>
        {list.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              class="button secondary"
              aria-current={seeing?.viewer === v.id ? "true" : undefined}
              onClick={() => onPick(v.id)}
            >
              {v.name}
            </button>
          </li>
        ))}
      </ul>
      {failed && <p role="status">{words.seeAsLoadFailed}</p>}
      {total > list.length && (
        <p class="small muted" role="status">
          {words.seeAsMore(list.length, total)}
        </p>
      )}
    </div>
  );
}

/** Find a name by typing; choosing one puts it in the middle. */
function CentrePicker({
  api,
  refId,
  current,
  onPick,
  asPlayers,
  view,
}: {
  api: AccountApi;
  refId: string;
  current: string;
  onPick: (id: string) => void;
  /** Looking as the players do (a player, or a DM in Players View): offered only what the players know. */
  asPlayers: boolean;
  view: WebView | undefined;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<NameRow[] | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    // The All names list is the DM's whole list: it holds names the players don't know, so anyone looking
    // as the players do is offered names from the Story graph's own door instead (#2989). With an API
    // that cannot do that, they are offered nothing rather than the whole list.
    const search = asPlayers ? api.webFind : api.allNames;
    if (q.trim() === "" || !search) {
      setFound(null);
      return;
    }
    const mine = ++latest.current;
    const timer = setTimeout(() => {
      const asked: Promise<{ id: string; name: string }[]> = asPlayers
        ? api.webFind!(refId, view ? { q, view } : { q })
        : api.allNames!(refId, { q, kind: "", sort: "name", page: 1 }).then((page) => page.names.slice(0, 8));
      void asked
        .then((names) => {
          if (mine === latest.current) setFound(names.slice(0, 8) as NameRow[]);
        })
        .catch(() => {
          if (mine === latest.current) setFound([]);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [api, refId, q, asPlayers, view]);

  return (
    <div class="field">
      <label>
        {words.centreSearch}
        <input
          type="search"
          value={q}
          autoComplete="off"
          spellcheck={false}
          aria-describedby="web-centre-hint"
          onInput={(e) => setQ((e.target as HTMLInputElement).value)}
        />
      </label>
      <p id="web-centre-hint" class="small muted">
        {current ? words.centreNow(current) : words.centreHint}
      </p>
      {found !== null && (
        <ul class="web-found" aria-label={words.centreLabel}>
          {found.length === 0 && <li class="muted">{words.centreNone}</li>}
          {found.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                class="button secondary"
                onClick={() => {
                  setQ("");
                  setFound(null);
                  onPick(n.id);
                }}
              >
                {n.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A name's spelling and kind, for a DM. Both go through the same route as the Names page. */
/** The player's "Peek behind the DM screen" switch. Greyed out, with the reason, when the DM turned
 * peeking off; it stays focusable so a screen reader can read why. */
function PeekSwitch({
  on,
  disabled,
  inputRef,
  onTurnOn,
  onTurnOff,
}: {
  on: boolean;
  disabled: boolean;
  inputRef: { current: HTMLInputElement | null };
  onTurnOn: () => void;
  onTurnOff: () => void;
}) {
  return (
    <div class="web-peek">
      <label class={disabled ? "choice web-peek-off" : "choice"}>
        <input
          type="checkbox"
          role="switch"
          ref={inputRef}
          checked={on && !disabled}
          aria-disabled={disabled ? "true" : undefined}
          aria-describedby={disabled ? "web-peek-note" : undefined}
          onClick={(e) => {
            if (disabled) {
              e.preventDefault();
              return;
            }
            // The box flips by itself on a click; what it shows is decided by the page.
            e.preventDefault();
            if (on) onTurnOff();
            else onTurnOn();
          }}
        />
        <span>{words.peekSwitch}</span>
      </label>
      {!on && <p class="small muted">{words.normalView}</p>}
      {disabled && (
        <p id="web-peek-note" class="small">
          {words.peekOffNote}
        </p>
      )}
    </div>
  );
}

/** The warning before a player peeks (#2405). Cancel changes nothing. */
function PeekAsk({ onYes, onCancel }: { onYes: () => void; onCancel: () => void }) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div
      class="panel stack"
      role="alertdialog"
      aria-labelledby="web-peek-title"
      aria-describedby="web-peek-warn"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <h2 id="web-peek-title">{words.peekTitle}</h2>
      <p id="web-peek-warn">{words.peekWarn}</p>
      <div class="row">
        <button type="button" class="button" onClick={onYes}>
          {words.peekYes}
        </button>
        <button type="button" class="button secondary" ref={cancel} onClick={onCancel}>
          {words.peekCancel}
        </button>
      </div>
    </div>
  );
}

/** @jsxImportSource preact */
// The Story Graph's map (#2324, #2934): names as shapes, connections as lines, drawn as plain SVG.
// Names settle apart by themselves (webForce.ts) and can be dragged; a name that was moved stays where it
// was dropped, remembered in this browser only (webPrefs.ts). Lines say their words only for the name
// the pointer or the keyboard is on, so a crowded map stays readable. Pan by dragging the background, zoom
// by wheel, pinch or the buttons. The same names and connections are listed under the map for screen
// readers. For the keyboard each name is a stop (Enter puts it in the middle, arrow keys move it), and
// the lines of the name in focus become stops too, so a map of hundreds of lines is not hundreds of Tabs.
import type { ComponentChildren } from "preact";
import { useEffect, useId, useRef, useState } from "preact/hooks";

import { mapReadLine } from "./webReading";

import { kindWords, webPage as words } from "../content/web-words";
import { kindStyle } from "./webKinds";
import NodeSummary from "./NodeSummary";
import type { AccountApi, IconChoice, WebEdge, WebNode } from "./api";
import {
  MAX_SIM_NAMES,
  MAX_SIM_STEPS,
  CALM,
  COOLING,
  extentOf,
  forceStep,
  LINK_KINDS,
  edgeKind,
  kindAnchors,
  NO_QUEST,
  orderedKinds,
  questGroups,
  linkPairs,
  settle,
  startNodes,
  type LayoutMode,
  type SimNode,
} from "./webForce";
import { PC_ICONS_LIVE } from "../content/pc-icons-switch";
import { pcIconWords } from "../content/pc-icon-words";
import LazyPanel from "./LazyPanel";
import { DEFAULT_LOOK, readMode, readPlaces, saveMode, savePlaces, type GraphLook } from "./webPrefs";
import { isTheory, zoomAbout, type View } from "./webLayout";
import { dimmedName, markSecret } from "./know";

const SHAPE_R = 24; // half the width of a name's shape: 48 px across at normal size, a fine thing to tap
const STEPS_PER_FRAME = 2; // the settle runs a couple of steps each frame: calm, and quick to finish
const DOUBLE_MS = 400; // two clicks on one name within this are a double click
/** A small arrowhead just outside the other name's shape, pointing away from the name in focus. */
function arrowhead(from: { x: number; y: number }, to: { x: number; y: number }, scale = 1): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const back = SHAPE_R * scale * 1.25 + 4; // (clear of the shape, even the bigger one of the middle name)
  const tx = to.x - ux * back;
  const ty = to.y - uy * back;
  const bx = tx - ux * 11;
  const by = ty - uy * 11;
  return `M${tx.toFixed(1)} ${ty.toFixed(1)}L${(bx - uy * 5).toFixed(1)} ${(by + ux * 5).toFixed(1)}L${(bx + uy * 5).toFixed(1)} ${(by - ux * 5).toFixed(1)}Z`;
}
/** A line's width by how many connections it holds (#3620): 2, 3, 4.5 and 6 px for one, two, three and four or more, so
 * one busy pair never swamps the map; the chosen line is two wider so it still stands out. Nothing set when off. */
export function lineWidthOf(count: number, on: boolean, chosen: boolean): { strokeWidth: number } | undefined {
  if (!on) return undefined;
  const base = [2, 3, 4.5, 6][Math.min(Math.max(count, 1), 4) - 1] ?? 2;
  return { strokeWidth: chosen ? base + 2 : base };
}
const NUDGE = 24; // how far an arrow key moves the name it is on

/** How many of a line's connections its tooltip lists before "plus n more…" (#3279). */
const TIP_LINKS = 4;

/** Each style has its own shape (and the plain ones a dashed edge), so a name is known by shape and word as
 * well as by its colour. */
function Shape({ kind, r }: { kind: string; r: number }) {
  const poly = (pts: [number, number][]): string => pts.map(([x, y]) => `${x * r},${y * r}`).join(" ");
  const ring = (n: number, k: number, turn = 0): string =>
    poly(Array.from({ length: n }, (_, i) => [Math.cos(turn + (2 * Math.PI * i) / n) * k, Math.sin(turn + (2 * Math.PI * i) / n) * k]));
  switch (kindStyle(kind)) {
    case "place":
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={2} />;
    case "group":
      return <polygon points={poly([[0, -1.2], [1.2, 0], [0, 1.2], [-1.2, 0]])} />;
    case "item":
      return <polygon points={poly([[0, -1.15], [1.1, 0.85], [-1.1, 0.85]])} />;
    case "event":
      return <polygon points={ring(6, 1.1)} />;
    case "creature":
      return <polygon points={ring(8, 1.1, Math.PI / 8)} />;
    case "quest": {
      // a five-pointed star: the long and the short points take turns
      const pts: [number, number][] = Array.from({ length: 10 }, (_, i) => {
        const k = i % 2 === 0 ? 1.3 : 0.55;
        const a = -Math.PI / 2 + (Math.PI * i) / 5;
        return [Math.cos(a) * k, Math.sin(a) * k];
      });
      return <polygon points={poly(pts)} />;
    }
    case "player_character":
      return (
        <>
          <circle r={r} />
          <circle r={r * 0.45} class="web-pc-ring" />
        </>
      );
    case "other":
      return <circle r={r} stroke-dasharray="2 3" stroke-width="2.5" />;
    default:
      return <circle r={r} />;
  }
}

/** A player character's icon (#3622): its picture clipped to the circle with a plain border, 👤 when it has none or the picture
 * does not load, and nothing at all when the icon is hidden. The browser fetches the picture after the drawing is up (an SVG image has no lazy attribute, but it never blocks the paint). */
function PcIcon({ node, r }: { node: WebNode; r: number }) {
  const [failed, setFailed] = useState<string | null>(null);
  const icon = node.icon;
  const clipId = useId();
  if (icon?.kind === "hidden") return null;
  // Only an https address is ever drawn (the door sends Discord's and dndbeyond.com's, never one a person typed); a failed
  // picture is remembered until its address changes, so a bad link is not retried on every draw.
  const url = icon?.url && /^(https:|data:image\/)/.test(icon.url) ? icon.url : null;
  const clip = `pcc-${clipId.replace(/[^A-Za-z0-9_-]/g, "_")}`; // (one per drawing of one name, never shared)
  if (url && failed !== url) {
    const k = r * 0.78;
    return (
      <g class="web-pc-icon" pointer-events="none">
        <clipPath id={clip}>
          <circle r={k} />
        </clipPath>
        <image href={url} x={-k} y={-k} width={2 * k} height={2 * k} clip-path={`url(#${clip})`} preserveAspectRatio="xMidYMid slice" role="img" aria-label={pcIconWords.alt(node.name)} onError={() => setFailed(url)} />
        <circle r={k} class="web-pc-icon-border" />
      </g>
    );
  }
  return (
    <text class="web-pc-person" y={r * 0.38} text-anchor="middle" font-size={r * 1.05} aria-hidden="true" pointer-events="none">
      👤
    </text>
  );
}

/** The groups of names, in the order their areas and anchors are given: kinds people first, quests as the door
 * sent them with "not part of a quest" last. */
function areaKeys(groups: string[], mode: LayoutMode): string[] {
  if (mode === "quests") return [...new Set(groups)].sort((a, b) => Number(a === NO_QUEST) - Number(b === NO_QUEST));
  return orderedKinds(groups);
}

const short = (text: string, max = 18): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

const reducedMotion = (): boolean => {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
};

const SIDE_KEY = "dmbot.web.details";
function readSide(): "left" | "right" | "under" {
  try {
    const v = localStorage.getItem(SIDE_KEY);
    if (v === "left" || v === "under") return v;
  } catch {
    // (no storage: the default)
  }
  return "right";
}

export default function WebMap({
  nodes,
  edges,
  center,
  refId,
  selected,
  onCenter,
  onLine,
  onNodePick,
  onIcon,
  iconUndo = null,
  look = DEFAULT_LOOK,
  canEdit = false,
  children,
  api,
  onDragging,
}: {
  /** The summary of a picked name (#3243); missing from an older page, so the panel shows none. */
  api?: AccountApi;
  /** Told when a drag of a name really starts (it has moved) and when it ends (#3324). */
  onDragging?: (on: boolean) => void;
  nodes: WebNode[];
  edges: WebEdge[];
  center: string;
  /** The campaign, so the places of moved names are kept for this campaign only. */
  refId: string;
  /** The chosen line's names, if a line is chosen. */
  selected: { a: string; b: string } | null;
  onCenter: (id: string) => void;
  onLine: (a: string, b: string) => void;
  /** Told when a name is picked, so a line that was chosen gives the panel back to the name. */
  onNodePick?: () => void;
  /** Choose a player character's icon (#3622): a plain-words problem, or null when it was saved. */
  onIcon?: ((id: string, choice: IconChoice) => Promise<string | null>) | undefined;
  /** The id of the character whose last icon change can be taken back, and the Undo itself. */
  iconUndo?: { id: string; now: IconChoice; undo: () => Promise<string | null> } | null;
  /** How the viewer likes the map drawn (#3620): spacing, sizes and line width. Today's look when missing. */
  look?: GraphLook;
  /** Only the campaign's DM is offered the links to the full editor (the Names page is the DM's list). */
  canEdit?: boolean;
  /** The chosen line's panel: drawn inside the map so it stays visible in full screen. */
  children?: ComponentChildren;
}) {
  const [mode, setMode] = useState<LayoutMode>(readMode);
  // The size of a name's shape and its words follow the viewer's choice; the settle reads the spacing from a ref.
  const R = SHAPE_R * look.nodeSize;
  const spreadRef = useRef(look.spacing);
  spreadRef.current = look.spacing;
  // "Cluster by quest" needs the door's quests, which only a DM in Full View is sent: without them it is the spread layout.
  const hasQuests = nodes.some((n) => n.quests !== undefined);
  const modeNow: LayoutMode = mode === "quests" && !hasQuests ? "free" : mode;
  // Full screen (#2981): the map fills the window, for a campaign too big for the page's own space.
  const [full, setFull] = useState(false);
  // Where the details panel sits on a wide screen; a narrow one always puts it under the map (the style sheet decides).
  const [side, setSide] = useState<"left" | "right" | "under">(readSide);
  const chooseSide = (to: "left" | "right" | "under"): void => {
    setSide(to);
    try {
      localStorage.setItem(SIDE_KEY, to);
    } catch {
      // (a browser that won't keep it: the choice lasts until the page closes)
    }
  };
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [frame, setFrame] = useState(0);
  const [extent, setExtent] = useState(260);
  // The name the pointer or the keyboard is on: everything not joined to it fades.
  const [focus, setFocus] = useState<string | null>(null);
  // The name a click picked: the side panel stays on it (a phone has no hover), until the middle name changes.
  // It is tied to the middle name it was picked under, so a new middle name starts with the panel on that name
  // with no frame of the old one.
  const [pickedAt, setPickedAt] = useState<{ id: string; center: string } | null>(null);
  const picked = pickedAt && pickedAt.center === center ? pickedAt.id : null;
  if (pickedAt && pickedAt.center !== center) setPickedAt(null); // (forgotten for good, not only hidden while the middle differs)
  const setPicked = (id: string) => {
    onNodePick?.();
    setPickedAt({ id, center });
  };
  const touched = useRef(false);
  const lastTap = useRef<{ id: string; at: number } | null>(null);

  // Kinds of connection the person turned off with the chips: their lines are not drawn.
  const [off, setOff] = useState<Set<string>>(new Set());
  const sim = useRef<SimNode[]>([]);
  const heat = useRef({ alpha: 0, steps: 0 });
  const loop = useRef(0);
  const [firstPlaces] = useState(() => readPlaces(refId)); // read once, not on every frame
  const places = useRef(firstPlaces);
  const svg = useRef<SVGSVGElement>(null);
  // A drag is not a tap: after one, the click that follows it is ignored.
  const dragged = useRef(false);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<number | null>(null);
  const dragName = useRef<{ id: string; x: number; y: number; moved: boolean } | null>(null);
  // Whether a name is being dragged now, reported to the page once per drag (so its auto update does not redraw
  // under a hand, #3324). The callback is read from a ref, because the window listeners are added once.
  const draggingOn = useRef(false);
  const onDraggingRef = useRef(onDragging);
  onDraggingRef.current = onDragging;
  const setDraggingOn = (on: boolean): void => {
    if (draggingOn.current === on) return;
    draggingOn.current = on;
    onDraggingRef.current?.(on);
  };
  const viewRef = useRef(view);
  viewRef.current = view;

  const units = (): number => {
    // Drawing units per screen pixel, so a drag of N pixels moves the drawing N pixels.
    const box = svg.current?.getBoundingClientRect();
    const side = box ? Math.min(box.width, box.height) : 0;
    return side > 0 ? (2 * extent) / side : 1;
  };
  const toDrawing = (clientX: number, clientY: number): { x: number; y: number } => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0) return { x: 0, y: 0 };
    const u = units();
    return {
      x: (clientX - box.left - box.width / 2) * u,
      y: (clientY - box.top - box.height / 2) * u,
    };
  };

  // A new spacing from the settings warms the settle up again, so the names move to where it puts them.
  const firstSpacing = useRef(true);
  useEffect(() => {
    if (firstSpacing.current) {
      firstSpacing.current = false;
      return;
    }
    reheat(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [look.spacing]);

  const zoom = (factor: number, at = { x: 0, y: 0 }): void => setView((v) => zoomAbout(v, at, factor));

  /** Keep the places of every pinned name, for this campaign, in this browser. */
  const remember = (): void => {
    // Merge, so a moved name that is not on screen now (fewer steps, another middle) keeps its place.
    const next: Record<string, [number, number]> = { ...places.current };
    for (const n of sim.current) if (n.pinned) next[n.id] = [Math.round(n.x), Math.round(n.y)];
    places.current = next;
    savePlaces(refId, next);
  };

  /** Warm the settle up again, and run it a couple of steps a frame until the names are calm. */
  const reheat = (alpha: number): void => {
    heat.current = { alpha: Math.max(heat.current.alpha, alpha), steps: 0 };
    if (sim.current.length > MAX_SIM_NAMES) return;
    if (reducedMotion()) {
      // Less motion: names move to their new places at once, with no animation, after a drag as at the start.
      const list = sim.current;
      settle(list, linksRef.current, list.findIndex((n) => n.id === centerRef.current), modeRef.current, anchorsRef.current, spreadRef.current);
      setExtent(extentOf(list));
      setFrame((f) => f + 1);
      return;
    }
    if (loop.current) return;
    const tick = (): void => {
      const list = sim.current;
      const centreAt = list.findIndex((n) => n.id === centerRef.current);
      let moved = 0;
      for (let i = 0; i < STEPS_PER_FRAME; i += 1) {
        moved = forceStep(list, linksRef.current, centreAt, modeRef.current, anchorsRef.current, heat.current.alpha, spreadRef.current);
        heat.current.alpha *= COOLING;
        heat.current.steps += 1;
      }
      setFrame((f) => f + 1);
      const calm = heat.current.alpha < 0.3 && moved < CALM;
      if (calm || heat.current.steps >= MAX_SIM_STEPS || list.length === 0) {
        loop.current = 0;
        setExtent(extentOf(list));
        return;
      }
      loop.current = requestAnimationFrame(tick);
    };
    loop.current = requestAnimationFrame(tick);
  };

  const centerRef = useRef(center);
  centerRef.current = center;
  const modeRef = useRef(mode);
  modeRef.current = modeNow;
  const linksRef = useRef<[number, number][]>([]);
  const anchorsRef = useRef(new Map<string, { x: number; y: number }>());

  // The names are placed again whenever the web, the middle name or the layout changes. A name that is
  // still here keeps where it is, so choosing another middle does not shuffle the whole map.
  // The parent builds fresh arrays on every render; only a real change to the names or lines may re-place them,
  // or choosing a line would shake the whole map.
  const sig = `${nodes.map((n) => n.id).join(",")}|${edges.map((x) => `${x.a}>${x.b}:${x.links.length}:${edgeKind(x)}`).join(",")}`;
  useEffect(() => {
    setFocus((f) => (f !== null && nodes.some((n) => n.id === f) ? f : null));
    const before = new Map(sim.current.map((n) => [n.id, n]));
    const next = startNodes(nodes, edges, center, places.current);
    if (modeNow === "quests") {
      const by = questGroups(nodes);
      for (const n of next) n.group = by.get(n.id) ?? NO_QUEST;
    }
    for (const n of next) {
      const old = before.get(n.id);
      if (old && !n.pinned) {
        n.x = old.x;
        n.y = old.y;
      }
    }
    sim.current = next;
    linksRef.current = linkPairs(next, edges);
    anchorsRef.current = kindAnchors(areaKeys(next.filter((n) => n.id !== center).map((n) => n.group), modeNow));
    if (loop.current) cancelAnimationFrame(loop.current);
    loop.current = 0;
    if (reducedMotion() || next.length > MAX_SIM_NAMES) {
      // Less motion: place the names at once. A very big web keeps its rings and is only moved by hand.
      settle(next, linksRef.current, next.findIndex((n) => n.id === center), modeNow, anchorsRef.current, spreadRef.current);
      setExtent(extentOf(next));
      setFrame((f) => f + 1);
    } else {
      // Draw the tidy starting rings at once, so the map is never blank while it settles.
      setExtent(extentOf(next));
      setFrame((f) => f + 1);
      heat.current = { alpha: 0, steps: 0 };
      reheat(1);
    }
    return () => {
      if (loop.current) cancelAnimationFrame(loop.current);
      loop.current = 0;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, center, modeNow]);

  // The window listeners are added once and removed on unmount, so a drag that ends off the map, or a
  // map that disappears mid-drag, leaves nothing behind. Handlers read the latest values through refs.
  const unitsRef = useRef(units);
  unitsRef.current = units;
  const toDrawingRef = useRef(toDrawing);
  toDrawingRef.current = toDrawing;
  const reheatRef = useRef(reheat);
  reheatRef.current = reheat;
  const rememberRef = useRef(remember);
  rememberRef.current = remember;
  // A tap on a name: one picks it (the panel under the map follows), a second on the same name within DOUBLE_MS puts it
  // in the middle. Counted here, not with `dblclick`, which a phone does not send reliably. Worked out when the pointer
  // is let go (below), not from the browser's `click`: the names keep drifting while the layout settles, and a
  // `click` is only sent to a name when the pointer is let go over the same element it went down on, so a name that
  // moved a little under the pointer got no click, and so no panel and no double click.
  const tap = (id: string): void => {
    // A finger leaves no hover behind: the panel follows the pick, not the last name touched.
    if (touched.current) setFocus(null);
    const now = Date.now();
    const again = lastTap.current?.id === id && now - lastTap.current.at < DOUBLE_MS;
    lastTap.current = again ? null : { id, at: now };
    if (again) onCenter(id);
    else setPicked(id);
  };
  const tapRef = useRef(tap);
  tapRef.current = tap;
  const tapped = useRef(false); // set while the `click` that follows a tap handled on pointer up is still to come
  const start = useRef(new Map<number, { x: number; y: number }>());
  useEffect(() => {
    const pair = (): [{ x: number; y: number }, { x: number; y: number }] =>
      [...pointers.current.values()].slice(0, 2) as [{ x: number; y: number }, { x: number; y: number }];
    const move = (m: PointerEvent): void => {
      const before = pointers.current.get(m.pointerId);
      if (before) pointers.current.set(m.pointerId, { x: m.clientX, y: m.clientY });
      // Two fingers are a pinch, wherever they landed (on a name or on the empty space): it comes before a
      // name's drag and before panning, so a pinch that starts on a name still zooms (#3015).
      if (pointers.current.size >= 2 && pinch.current) {
        const [p, q] = pair();
        const now = Math.hypot(p.x - q.x, p.y - q.y);
        if (now > 0) {
          setView((v) => zoomAbout(v, toDrawingRef.current((p.x + q.x) / 2, (p.y + q.y) / 2), now / (pinch.current ?? now)));
          pinch.current = now;
          dragged.current = true;
        }
        return;
      }
      const grab = dragName.current;
      if (grab) {
        if (!grab.moved && Math.hypot(m.clientX - grab.x, m.clientY - grab.y) <= 5) return;
        grab.moved = true;
        setDraggingOn(true);
        dragged.current = true;
        const n = sim.current.find((q) => q.id === grab.id);
        if (!n) return;
        const at = toDrawingRef.current(m.clientX, m.clientY);
        const v = viewRef.current;
        n.x = (at.x - v.x) / v.k;
        n.y = (at.y - v.y) / v.k;
        n.vx = 0;
        n.vy = 0;
        n.pinned = true;
        reheatRef.current(0.4);
        setFrame((f) => f + 1);
        return;
      }
      if (!before) return;
      const from = start.current.get(m.pointerId);
      if (from && Math.hypot(m.clientX - from.x, m.clientY - from.y) > 5) dragged.current = true;
      const u = unitsRef.current();
      const dx = (m.clientX - before.x) * u;
      const dy = (m.clientY - before.y) * u;
      setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    };
    const up = (u: PointerEvent): void => {
      pointers.current.delete(u.pointerId);
      start.current.delete(u.pointerId);
      if (pointers.current.size < 2) pinch.current = null;
      if (dragName.current) {
        const grab = dragName.current;
        dragName.current = null;
        setDraggingOn(false);
        if (grab.moved) {
          rememberRef.current();
          setExtent(extentOf(sim.current));
        } else if (u.type === "pointerup") {
          // Let go without having dragged: a tap on that name, wherever the pointer is now (not for a cancel, which
          // is the browser taking the touch over to scroll).
          tapRef.current(grab.id);
          tapped.current = true;
          setTimeout(() => (tapped.current = false), 0);
        }
        setTimeout(() => (dragged.current = false), 0);
        return;
      }
      // The click that follows a drag arrives after this; forget the drag only once it has passed.
      if (pointers.current.size === 0) setTimeout(() => (dragged.current = false), 0);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      pointers.current.clear();
      // Going away mid-drag ends it, so the page is not left waiting for a drag that will never end.
      setDraggingOn(false);
    };
  }, []);

  const down = (e: PointerEvent): void => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    start.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) dragged.current = false;
    if (pointers.current.size === 2) {
      const [p, q] = [...pointers.current.values()] as [{ x: number; y: number }, { x: number; y: number }];
      pinch.current = Math.hypot(p.x - q.x, p.y - q.y);
      // A second finger turns a name's drag into a pinch: the name stays where it was.
      dragName.current = null;
      setDraggingOn(false);
    }
  };

  // Escape leaves full screen, and the page behind does not scroll while it is on.
  useEffect(() => {
    if (!full) return undefined;
    const leave = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setFull(false);
    };
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", leave);
    return () => {
      window.removeEventListener("keydown", leave);
      document.body.style.overflow = before;
    };
  }, [full]);

  const chooseMode = (next: LayoutMode): void => {
    if (next === mode) return;
    saveMode(next);
    setMode(next);
  };
  const letGo = (): void => {
    for (const n of sim.current) n.pinned = false;
    places.current = {};
    savePlaces(refId, {});
    reheat(0.9);
    setFrame((f) => f + 1);
  };
  /** An arrow key moves the name it is on a little and keeps it there: the keyboard's way to drag. */
  const nudge = (id: string, dx: number, dy: number): void => {
    const n = sim.current.find((q) => q.id === id);
    if (!n) return;
    n.x += dx;
    n.y += dy;
    n.vx = 0;
    n.vy = 0;
    n.pinned = true;
    remember();
    setExtent(extentOf(sim.current));
    setFrame((f) => f + 1);
  };

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const at = new Map(sim.current.map((n) => [n.id, n]));
  const near = new Set<string>();
  if (focus) {
    near.add(focus);
    for (const e of edges) {
      if (off.has(edgeKind(e))) continue; // a line that is not drawn lights nothing
      if (e.a === focus) near.add(e.b);
      if (e.b === focus) near.add(e.a);
    }
  }
  const e = extent;
  // Every kind drawn, people first, each once (the legend shows its colour, shape and word).
  const kindsHere = orderedKinds(nodes.map((n) => n.kind));
  const anyPinned = sim.current.some((n) => n.pinned);
  const kindsPresent = LINK_KINDS.filter((k) => edges.some((e) => edgeKind(e) === k));
  const tooBig = nodes.length > MAX_SIM_NAMES;
  // The side panel: the name that was clicked or tapped (never one the pointer only passes over), or the middle name, with its connections in words.
  // A picked or focused name that is no longer drawn falls back to the next, never to an empty panel.
  const detail = byId.get(picked ?? "") ?? byId.get(center) ?? null;
  const detailPinned = detail ? (at.get(detail.id)?.pinned ?? false) : false;
  const sideLines = detail
    ? edges
        .filter((x) => (x.a === detail.id || x.b === detail.id) && !off.has(edgeKind(x)))
        .flatMap((x) =>
          x.links.map((l) => ({
            id: l.id,
            kind: l.kind,
            secret: l.secret,
            ...(l.knowWord ? { knowWord: l.knowWord } : {}),
            belief: l.belief,
            text: words.linkText(byId.get(x.a)?.name ?? "", byId.get(x.b)?.name ?? "", l.words, l.direction),
          })),
        )
    : [];
  // The name of each quest, from the nodes that say they are in it and from the quests drawn.
  const questNames = new Map<string, string>(nodes.flatMap((n) => [...(n.quests ?? []).map((q): [string, string] => [q.id, q.name]), ...(kindStyle(n.kind) === "quest" ? [[n.id, n.name] as [string, string]] : [])]));
  void frame; // each frame of the settle re-draws the map from the names' new places

  return (
    <div class={`web-map${full ? " web-full" : ""}`}>
      <div class="row web-tools">
        <button type="button" class="button secondary" onClick={() => zoom(1.4)} aria-label={words.zoomIn}>
          +
        </button>
        <button type="button" class="button secondary" onClick={() => zoom(1 / 1.4)} aria-label={words.zoomOut}>
          −
        </button>
        <button type="button" class="button secondary" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
          {words.resetView}
        </button>
        <div class="web-seg" role="group" aria-label={words.layoutLabel}>
          <button type="button" class="button secondary" disabled={tooBig} aria-pressed={modeNow === "free"} onClick={() => chooseMode("free")}>
            {words.layoutFree}
          </button>
          <button type="button" class="button secondary" disabled={tooBig} aria-pressed={modeNow === "groups"} onClick={() => chooseMode("groups")}>
            {words.layoutGroups}
          </button>
          {hasQuests && (
            <button type="button" class="button secondary" disabled={tooBig} aria-pressed={modeNow === "quests"} onClick={() => chooseMode("quests")}>
              {words.layoutQuests}
            </button>
          )}
        </div>
        <div class="web-seg web-side-seg" role="group" aria-label={words.sideLabel}>
          {(["left", "right", "under"] as const).map((k) => (
            <button key={k} type="button" class="button secondary" aria-pressed={side === k} onClick={() => chooseSide(k)}>
              {k === "left" ? words.sideLeft : k === "right" ? words.sideRight : words.sideUnder}
            </button>
          ))}
        </div>
        <button type="button" class="button secondary" aria-pressed={full} onClick={() => setFull((f) => !f)}>
          {full ? words.leaveFullScreen : words.fullScreen}
        </button>
        <button type="button" class="button secondary" disabled={!anyPinned} onClick={letGo}>
          {words.letGo}
        </button>
      </div>
      {kindsPresent.length > 0 && (
        <div class="web-chips" role="group" aria-label={words.kindLabel}>
          {kindsPresent.map((k) => (
            <button
              key={k}
              type="button"
              class={`button secondary web-chip web-link-${k}`}
              aria-pressed={!off.has(k)}
              onClick={() =>
                setOff((was) => {
                  const next = new Set(was);
                  if (next.has(k)) next.delete(k);
                  else next.add(k);
                  return next;
                })
              }
            >
              <span class="web-dot" aria-hidden="true" /> {words.linkKind[k]}
              {off.has(k) && <span class="visually-hidden"> {words.hiddenMark}</span>}
            </button>
          ))}
        </div>
      )}
      {kindsPresent.length > 0 && <p class="small muted">{words.kindHint}</p>}
      <p class="small muted">{tooBig ? words.layoutTooBig : words.panHint}</p>
      <div class={`web-stage web-side-${side}`}>
      <svg
        ref={svg}
        class="web-svg"
        style={look.textSize === 1 ? undefined : { "--web-text": `${14 * look.textSize}px` }}
        viewBox={`${-e} ${-e} ${2 * e} ${2 * e}`}
        role="group"
        aria-label={words.mapLabel(nodes.length, byId.get(center)?.name ?? "")}
        onPointerDownCapture={down}
        onWheel={(ev) => {
          if (!ev.ctrlKey && !ev.metaKey) return; // plain scrolling belongs to the page
          ev.preventDefault();
          zoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15, toDrawing(ev.clientX, ev.clientY));
        }}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {modeNow !== "free" &&
            areaKeys(sim.current.filter((n) => n.id !== center).map((n) => n.group), modeNow).map((k) => {
              const members = sim.current.filter((n) => n.group === k && n.id !== center);
              if (members.length === 0) return null;
              const cx = members.reduce((t, n) => t + n.x, 0) / members.length;
              const cy = members.reduce((t, n) => t + n.y, 0) / members.length;
              const r = members.reduce((t, n) => Math.max(t, Math.hypot(n.x - cx, n.y - cy) + 60), 64);
              const quest = modeNow === "quests";
              return (
                <g key={k} class={`web-area web-kind-${quest ? (k === NO_QUEST ? "other" : "quest") : kindStyle(k)}`} aria-hidden="true">
                  <circle cx={cx} cy={cy} r={r} />
                  <text x={cx} y={cy - r - 6} text-anchor="middle" class="web-area-text">
                    {quest ? (k === NO_QUEST ? words.noQuest : (questNames.get(k) ?? k)) : kindWords(k)}
                  </text>
                </g>
              );
            })}
          {edges.map((edge) => {
            const p = at.get(edge.a);
            const q = at.get(edge.b);
            if (!p || !q) return null;
            const kind = edgeKind(edge);
            if (off.has(kind)) return null;
            const chosen = selected !== null && ((selected.a === edge.a && selected.b === edge.b) || (selected.a === edge.b && selected.b === edge.a));
            const theory = isTheory(edge);
            const dim = edge.links.length > 0 && edge.links.every((l) => l.revealed === false);
            const touches = focus !== null && (edge.a === focus || edge.b === focus);
            const text = `${theory ? `${words.theoryMark} ` : ""}${edge.label}${edge.more > 0 ? ` (+${edge.more})` : ""}`;
            // Hovering a line that holds several connections lists four of them, then "plus n more…", so "(+6)" on the
            // line is never a mystery (#3279). The API sends the links oldest first and words the line with the latest
            // one, so the line's own connection comes first and the others follow newest first. Each reads in full, from
            // the right end ("Mira serves Gorn"), as the side panel reads it. One connection keeps its words.
            const aName = byId.get(edge.a)?.name ?? "";
            const bName = byId.get(edge.b)?.name ?? "";
            const lead = edge.links.findLastIndex((l) => l.words === edge.label);
            const ordered = [...(lead >= 0 ? [edge.links[lead]!] : []), ...edge.links.filter((_, i) => i !== lead).reverse()];
            const tip =
              edge.links.length > 1
                ? [
                    ...ordered.slice(0, TIP_LINKS).map((l, i) => `${theory && i === 0 ? `${words.theoryMark} ` : ""}${words.linkText(aName, bName, l.words, l.direction)}`),
                    ...(ordered.length > TIP_LINKS ? [words.plusMore(ordered.length - TIP_LINKS)] : []),
                  ].join("\n")
                : text;
            // The line of the name in focus (hovered, else picked) says just the verb as that name would say it, with an
            // arrow toward the other name; with no wording from that end it says nothing and has no arrowhead. The
            // chosen line keeps its full words. A line that holds several connections says how many when it has no
            // verb to show, so a thick bundle is never mistaken for one.
            const from = focus ?? picked;
            const reads = from !== null && (edge.a === from || edge.b === from);
            const reading = reads ? mapReadLine(edge, from) : null;
            const many = edge.links.length > 1 ? words.nConnections(edge.links.length) : null;
            const shown = chosen
              ? text
              : reading
                ? reading.verb !== null
                  ? `${theory ? `${words.theoryMark} ` : ""}${reading.verb}${reading.more > 0 ? ` (+${reading.more})` : ""}`
                  : many
                : many;
            const arrowFrom = reading?.arrow && from !== null ? (from === edge.a ? p : q) : null;
            const arrowTo = arrowFrom === null ? null : arrowFrom === p ? q : p;
            const open = (): void => {
              if (!dragged.current) onLine(edge.a, edge.b);
            };
            const turn = (Math.atan2(q.y - p.y, q.x - p.x) * 180) / Math.PI;
            const upright = turn > 90 || turn < -90 ? turn + 180 : turn;
            const mx = (p.x + q.x) / 2;
            const my = (p.y + q.y) / 2;
            return (
              <g
                key={`${edge.a}|${edge.b}`}
                class={`web-edge web-link-${kind}${chosen ? " chosen" : ""}${theory ? " web-theory" : ""}${dim ? " web-dim" : ""}${focus && !touches ? " web-faded" : ""}`}
              >
                <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} class="web-line" style={lineWidthOf(edge.links.length, look.lineWidth, chosen)} />
                {/* A fat invisible line: easy to hit with a finger, and reachable by keyboard. */}
                <line
                  x1={p.x}
                  y1={p.y}
                  x2={q.x}
                  y2={q.y}
                  class="web-hit"
                  tabIndex={touches ? 0 : -1}
                  role="button"
                  aria-label={`${words.lineBetween(byId.get(edge.a)?.name ?? "", byId.get(edge.b)?.name ?? "")}${edge.links.length > 1 ? `. ${tip.replace(/\n/g, ". ")}` : ""}`}
                  onClick={open}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      onLine(edge.a, edge.b);
                    }
                  }}
                >
                  <title>{tip}</title>
                </line>
                {arrowFrom && arrowTo && <path d={arrowhead(arrowFrom, arrowTo, look.nodeSize)} class="web-arrow" aria-hidden="true" />}
                {shown && (
                  <text x={mx} y={my - 5} transform={`rotate(${upright.toFixed(1)} ${mx} ${my})`} class="web-edge-text" text-anchor="middle" onClick={open}>
                    <title>{tip}</title>
                    {short(shown, 26)}
                  </text>
                )}
              </g>
            );
          })}
          {nodes.map((n) => {
            const place = at.get(n.id);
            if (!place) return null;
            const faded = focus !== null && !near.has(n.id);
            return (
              <g
                key={n.id}
                class={`web-node web-kind-${kindStyle(n.kind)}${n.unnamed ? " web-unnamed" : ""}${n.id === center ? " centre" : ""}${dimmedName(n) ? " web-dim" : ""}${faded ? " web-faded" : ""}`}
                transform={`translate(${place.x.toFixed(1)} ${place.y.toFixed(1)})`}
                tabIndex={0}
                role="button"
                aria-label={words.nameButton(n.name)}
                onPointerDown={(ev) => {
                  ev.stopPropagation();
                  touched.current = ev.pointerType === "touch";
                  if (pointers.current.size > 1) return; // a second finger: a pinch, not a drag of this name
                  if (ev.button !== 0) return; // a right or middle press opens a menu or scrolls: not a tap or a drag
                  dragName.current = { id: n.id, x: ev.clientX, y: ev.clientY, moved: false };
                  dragged.current = false;
                }}
                onPointerEnter={() => setFocus(n.id)}
                onPointerLeave={() => setFocus((f) => (f === n.id ? null : f))}
                onFocus={() => setFocus(n.id)}
                onBlur={() => setFocus((f) => (f === n.id ? null : f))}
                onClick={() => {
                  if (tapped.current) return; // (this click was already counted when the pointer was let go)
                  if (dragged.current) {
                    lastTap.current = null; // (the end of a drag is neither a click nor half of a double click)
                    return;
                  }
                  tap(n.id);
                }}
                onKeyDown={(ev) => {
                  const step: Record<string, [number, number]> = {
                    ArrowLeft: [-NUDGE, 0],
                    ArrowRight: [NUDGE, 0],
                    ArrowUp: [0, -NUDGE],
                    ArrowDown: [0, NUDGE],
                  };
                  const by = step[ev.key];
                  if (by) {
                    ev.preventDefault();
                    nudge(n.id, by[0], by[1]);
                  } else if (ev.key === "Enter" || ev.key === " ") {
                    ev.preventDefault();
                    setPicked(n.id); // (the panel's "Put it in the middle" button is the keyboard's double click)
                  }
                }}
              >
                <title>{n.name}</title>
                <Shape kind={n.kind} r={n.id === center ? R * 1.25 : R} />
                {PC_ICONS_LIVE && kindStyle(n.kind) === "player_character" && <PcIcon node={n} r={n.id === center ? R * 1.25 : R} />}
                {place.pinned && <circle class="web-pin" r={5} cx={R * 0.8} cy={-R * 0.8} aria-hidden="true" />}
                <text y={R * 1.25 + 18 * look.textSize} text-anchor="middle" class="web-node-text">
                  {markSecret(n) ? `${words.secretMark} ` : ""}
                  {short(n.name)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      {selected && children ? (
        <div class="web-side">{children}</div>
      ) : detail && (
        <aside class="web-side panel stack" aria-labelledby="web-side-h" aria-live="polite">
          <h3 id="web-side-h">{detail.name}</h3>
          <p class="small muted">{kindWords(detail.kind)}</p>
          {PC_ICONS_LIVE && onIcon && detail.canSetIcon && kindStyle(detail.kind) === "player_character" && (
            <LazyPanel
              key={detail.id}
              load={() => import("./PcIconRow")}
              what="the icon choices"
              props={{ node: detail, onChoose: (choice: IconChoice) => onIcon(detail.id, choice), undo: iconUndo?.id === detail.id && iconUndo.now === (detail.iconChoice ?? "default"), onUndo: iconUndo?.undo ?? (async () => null) }}
            />
          )}
          {api && <NodeSummary key={detail.id} api={api} refId={refId} id={detail.id} />}
          {(detail.quests?.length ?? 0) > 1 && <p class="small">{words.alsoIn(detail.quests!.slice(1).map((q) => q.name).join(", "))}</p>}
          {sideLines.length === 0 ? (
            <p class="small">{words.sideNone}</p>
          ) : (
            <ul class="stack small">
              {sideLines.map((l) => (
                <li key={l.id}>
                  {l.kind && (
                    <span class={`web-kindmark web-link-${l.kind}`}>
                      <span class="web-dot" aria-hidden="true" /> {words.linkKind[l.kind]}:{" "}
                    </span>
                  )}
                  {markSecret(l) && <span aria-hidden="true">{words.secretMark} </span>}
                  {l.belief && <span aria-hidden="true">{words.theoryMark} </span>}
                  {l.text}
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            class="button secondary"
            aria-pressed={detailPinned}
            onClick={() => {
              const n = sim.current.find((q) => q.id === detail.id);
              if (!n) return;
              n.pinned = !n.pinned;
              if (!n.pinned) {
                const rest = { ...places.current };
                delete rest[n.id];
                places.current = rest;
                savePlaces(refId, rest);
                reheat(0.6);
              } else remember();
              setFrame((f) => f + 1);
            }}
          >
            {detailPinned ? words.letMove : words.keepHere}
          </button>
          {canEdit && (
            <a class="button secondary" href={`/account/names/${encodeURIComponent(refId)}#said=${encodeURIComponent(detail.name)}`}>
              {words.editFull(detail.name)}
            </a>
          )}
          {detail.id === center ? <p class="small muted">{words.inMiddle}</p> : <p class="small muted">{words.doubleTapHint}</p>}
          {detail.id !== center && (
            <button type="button" class="button secondary" onClick={() => onCenter(detail.id)}>
              {words.putInMiddle}
            </button>
          )}
        </aside>
      )}
      </div>
      <section aria-labelledby="web-legend" class="web-legend">
        <h3 id="web-legend" class="small">
          {words.legendHeading}
        </h3>
        <ul class="row small">
          {kindsHere.map((k) => (
            <li key={k}>
              <svg width="26" height="26" viewBox="-15 -15 30 30" aria-hidden="true" class={`web-kind-${kindStyle(k)}`}>
                <Shape kind={k} r={9} />
              </svg>{" "}
              {kindWords(k)}
            </li>
          ))}
          {kindsPresent.map((k) => (
            <li key={`kind-${k}`} class={`web-link-${k}`}>
              <svg width="34" height="12" viewBox="0 0 34 12" aria-hidden="true">
                <line x1="1" y1="6" x2="33" y2="6" class="web-line" />
              </svg>{" "}
              {words.linkKind[k]}
            </li>
          ))}
          {nodes.some((n) => n.revealed === false) && <li>{words.legendNotRevealed}</li>}
          {nodes.some((n) => n.knowWord === "secret") && (
            <li>
              {words.secretMark} {words.knowWords.secret}
            </li>
          )}
          {edges.some(isTheory) && <li>{words.legendTheory}</li>}
          {edges.some((e) => e.links.some((l) => l.pinned)) && <li>{words.legendPinned}</li>}
        </ul>
        {edges.some((e) => e.links.length > 0) && <p class="small muted">{words.legendReading}</p>}
      </section>
    </div>
  );
}

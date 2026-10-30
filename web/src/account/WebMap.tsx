/** @jsxImportSource preact */
// The Story Graph's map (#2324, #2934): names as shapes, connections as lines, drawn as plain SVG.
// Names settle apart by themselves (webForce.ts) and can be dragged; a name that was moved stays where it
// was dropped, remembered in this browser only (webPrefs.ts). Lines say their words only for the name
// the pointer or the keyboard is on, so a crowded map stays readable. Pan by dragging the background, zoom
// by wheel, pinch or the buttons. The same names and connections are listed under the map for screen
// readers. For the keyboard each name is a stop (Enter puts it in the middle, arrow keys move it), and
// the lines of the name in focus become stops too, so a map of hundreds of lines is not hundreds of Tabs.
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { kindWords, webPage as words } from "../content/web-words";
import { kindStyle } from "./webKinds";
import type { WebEdge, WebNode } from "./api";
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
import { readMode, readPlaces, saveMode, savePlaces } from "./webPrefs";
import { isTheory, zoomAbout, type View } from "./webLayout";

const SHAPE_R = 24; // half the width of a name's shape: 48 px across at normal size, a fine thing to tap
const STEPS_PER_FRAME = 2; // the settle runs a couple of steps each frame: calm, and quick to finish
const NUDGE = 24; // how far an arrow key moves the name it is on

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

export default function WebMap({
  nodes,
  edges,
  center,
  refId,
  selected,
  onCenter,
  onLine,
  children,
}: {
  nodes: WebNode[];
  edges: WebEdge[];
  center: string;
  /** The campaign, so the places of moved names are kept for this campaign only. */
  refId: string;
  /** The chosen line's names, if a line is chosen. */
  selected: { a: string; b: string } | null;
  onCenter: (id: string) => void;
  onLine: (a: string, b: string) => void;
  /** The chosen line's panel: drawn inside the map so it stays visible in full screen. */
  children?: ComponentChildren;
}) {
  const [mode, setMode] = useState<LayoutMode>(readMode);
  // "Cluster by quest" needs the door's quests, which only a DM in Full View is sent: without them it is the spread layout.
  const hasQuests = nodes.some((n) => n.quests !== undefined);
  const modeNow: LayoutMode = mode === "quests" && !hasQuests ? "free" : mode;
  // Full screen (#2981): the map fills the window, for a campaign too big for the page's own space.
  const [full, setFull] = useState(false);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [frame, setFrame] = useState(0);
  const [extent, setExtent] = useState(260);
  // The name the pointer or the keyboard is on: everything not joined to it fades.
  const [focus, setFocus] = useState<string | null>(null);
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
      settle(list, linksRef.current, list.findIndex((n) => n.id === centerRef.current), modeRef.current, anchorsRef.current);
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
        moved = forceStep(list, linksRef.current, centreAt, modeRef.current, anchorsRef.current, heat.current.alpha);
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
      settle(next, linksRef.current, next.findIndex((n) => n.id === center), modeNow, anchorsRef.current);
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
        if (grab.moved) {
          rememberRef.current();
          setExtent(extentOf(sim.current));
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
  // The side panel: the name in focus, or the middle name, with its connections in words.
  const detail = byId.get(focus ?? center) ?? null;
  const detailPinned = detail ? (at.get(detail.id)?.pinned ?? false) : false;
  const sideLines = detail
    ? edges
        .filter((x) => (x.a === detail.id || x.b === detail.id) && !off.has(edgeKind(x)))
        .flatMap((x) =>
          x.links.map((l) => ({
            id: l.id,
            kind: l.kind,
            secret: l.secret,
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
      <div class="web-stage">
      <svg
        ref={svg}
        class="web-svg"
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
            // Words only on the line of the name in focus or the chosen line; a line that holds several
            // connections says how many, so a thick bundle is never mistaken for one.
            const shown = touches || chosen ? text : edge.links.length > 1 ? words.nConnections(edge.links.length) : null;
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
                <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} class="web-line" />
                {/* A fat invisible line: easy to hit with a finger, and reachable by keyboard. */}
                <line
                  x1={p.x}
                  y1={p.y}
                  x2={q.x}
                  y2={q.y}
                  class="web-hit"
                  tabIndex={touches ? 0 : -1}
                  role="button"
                  aria-label={words.lineBetween(byId.get(edge.a)?.name ?? "", byId.get(edge.b)?.name ?? "")}
                  onClick={open}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      onLine(edge.a, edge.b);
                    }
                  }}
                />
                {shown && (
                  <text x={mx} y={my - 5} transform={`rotate(${upright.toFixed(1)} ${mx} ${my})`} class="web-edge-text" text-anchor="middle" onClick={open}>
                    <title>{text}</title>
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
                class={`web-node web-kind-${kindStyle(n.kind)}${n.id === center ? " centre" : ""}${n.revealed === false ? " web-dim" : ""}${faded ? " web-faded" : ""}`}
                transform={`translate(${place.x.toFixed(1)} ${place.y.toFixed(1)})`}
                tabIndex={0}
                role="button"
                aria-label={words.nameButton(n.name)}
                onPointerDown={(ev) => {
                  ev.stopPropagation();
                  if (pointers.current.size > 1) return; // a second finger: a pinch, not a drag of this name
                  dragName.current = { id: n.id, x: ev.clientX, y: ev.clientY, moved: false };
                  dragged.current = false;
                }}
                onPointerEnter={() => setFocus(n.id)}
                onPointerLeave={() => setFocus((f) => (f === n.id ? null : f))}
                onFocus={() => setFocus(n.id)}
                onBlur={() => setFocus((f) => (f === n.id ? null : f))}
                onClick={() => {
                  if (!dragged.current) onCenter(n.id);
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
                    onCenter(n.id);
                  }
                }}
              >
                <title>{n.name}</title>
                <Shape kind={n.kind} r={n.id === center ? SHAPE_R * 1.25 : SHAPE_R} />
                {place.pinned && <circle class="web-pin" r={5} cx={SHAPE_R * 0.8} cy={-SHAPE_R * 0.8} aria-hidden="true" />}
                <text y={SHAPE_R * 1.25 + 18} text-anchor="middle" class="web-node-text">
                  {n.secret ? `${words.secretMark} ` : ""}
                  {short(n.name)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      {detail && (
        <aside class="web-side panel stack" aria-labelledby="web-side-h" aria-live="polite">
          <h3 id="web-side-h">{detail.name}</h3>
          <p class="small muted">{kindWords(detail.kind)}</p>
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
                  {l.secret && <span aria-hidden="true">{words.secretMark} </span>}
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
        </aside>
      )}
      </div>
      {children}
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
          {edges.some(isTheory) && <li>{words.legendTheory}</li>}
          {edges.some((e) => e.links.some((l) => l.pinned)) && <li>{words.legendPinned}</li>}
        </ul>
      </section>
    </div>
  );
}

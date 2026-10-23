/** @jsxImportSource preact */
// The relationship web's map (#2324): names as shapes, connections as lines, drawn as plain SVG from the
// layout in webLayout.ts. Pan by dragging, zoom by wheel, pinch or the buttons. The map is for the eyes
// and the finger; the same names and connections are listed under it for the keyboard and for screen
// readers, so the drawing itself is hidden from them.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { kindWords, webPage as words } from "../content/web-words";
import type { WebEdge, WebNode } from "./api";
import { isTheory, radialLayout, zoomAbout, type View } from "./webLayout";

const SHAPE_R = 24; // half the width of a name's shape: 48 px across at normal size, a fine thing to tap
const SHOW_LABELS_UNDER = 80; // more lines than this and only the chosen line says its words

/** Kinds with a drawn shape; any other kind is a circle with a dashed edge. */
const KIND_ORDER = ["npc", "place", "group", "item", "event"];

function Shape({ kind, r }: { kind: string; r: number }) {
  switch (kind) {
    case "place":
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={5} />;
    case "group":
      return <polygon points={`0,${-r * 1.2} ${r * 1.2},0 0,${r * 1.2} ${-r * 1.2},0`} />;
    case "item":
      return <polygon points={`0,${-r * 1.15} ${r * 1.1},${r * 0.85} ${-r * 1.1},${r * 0.85}`} />;
    case "event": {
      const pts = [0, 1, 2, 3, 4, 5].map((i) => {
        const a = (Math.PI / 3) * i;
        return `${Math.cos(a) * r * 1.1},${Math.sin(a) * r * 1.1}`;
      });
      return <polygon points={pts.join(" ")} />;
    }
    default:
      return <circle r={r} stroke-dasharray={KIND_ORDER.includes(kind) ? undefined : "4 3"} />;
  }
}

const short = (text: string, max = 18): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

export default function WebMap({
  nodes,
  edges,
  center,
  selected,
  onCenter,
  onLine,
}: {
  nodes: WebNode[];
  edges: WebEdge[];
  center: string;
  /** The chosen line's names, if a line is chosen. */
  selected: { a: string; b: string } | null;
  onCenter: (id: string) => void;
  onLine: (a: string, b: string) => void;
}) {
  const layout = useMemo(() => radialLayout(nodes, edges, center), [nodes, edges, center]);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const svg = useRef<SVGSVGElement>(null);
  // A drag is not a tap: after one, the click that follows it is ignored.
  const dragged = useRef(false);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<number | null>(null);

  const units = (): number => {
    // Drawing units per screen pixel, so a drag of N pixels moves the drawing N pixels.
    const box = svg.current?.getBoundingClientRect();
    const side = box ? Math.min(box.width, box.height) : 0;
    return side > 0 ? (2 * layout.extent) / side : 1;
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

  // The window listeners are added once and removed on unmount, so a drag that ends off the map, or a
  // map that disappears mid-drag, leaves nothing behind. Handlers read the latest layout through refs.
  const unitsRef = useRef(units);
  unitsRef.current = units;
  const toDrawingRef = useRef(toDrawing);
  toDrawingRef.current = toDrawing;
  const start = useRef(new Map<number, { x: number; y: number }>());
  useEffect(() => {
    const pair = (): [{ x: number; y: number }, { x: number; y: number }] =>
      [...pointers.current.values()].slice(0, 2) as [{ x: number; y: number }, { x: number; y: number }];
    const move = (m: PointerEvent): void => {
      const before = pointers.current.get(m.pointerId);
      if (!before) return;
      pointers.current.set(m.pointerId, { x: m.clientX, y: m.clientY });
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
    }
  };

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const labels = edges.length <= SHOW_LABELS_UNDER;
  const e = layout.extent;
  const kindsHere = KIND_ORDER.filter((k) => nodes.some((n) => n.kind === k));
  const otherKinds = [...new Set(nodes.map((n) => n.kind))].filter((k) => !KIND_ORDER.includes(k));

  return (
    <div class="web-map">
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
        <span class="small muted">{words.panHint}</span>
      </div>
      <svg
        ref={svg}
        class="web-svg"
        viewBox={`${-e} ${-e} ${2 * e} ${2 * e}`}
        aria-hidden="true"
        aria-label={words.mapLabel(nodes.length, byId.get(center)?.name ?? "")}
        onPointerDown={down}
        onWheel={(ev) => {
          if (!ev.ctrlKey && !ev.metaKey) return; // plain scrolling belongs to the page
          ev.preventDefault();
          zoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15, toDrawing(ev.clientX, ev.clientY));
        }}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {layout.radii.slice(1).map((r, i) => (
            <circle key={i} r={r} class="web-ring" />
          ))}
          {edges.map((edge) => {
            const p = layout.points.get(edge.a);
            const q = layout.points.get(edge.b);
            if (!p || !q) return null;
            const chosen = selected !== null && ((selected.a === edge.a && selected.b === edge.b) || (selected.a === edge.b && selected.b === edge.a));
            const theory = isTheory(edge);
            const dim = edge.links.length > 0 && edge.links.every((l) => l.revealed === false);
            const text = `${theory ? `${words.theoryMark} ` : ""}${edge.label}${edge.more > 0 ? ` (+${edge.more})` : ""}`;
            const open = (): void => {
              if (!dragged.current) onLine(edge.a, edge.b);
            };
            return (
              <g
                key={`${edge.a}|${edge.b}`}
                class={`web-edge${chosen ? " chosen" : ""}${theory ? " web-theory" : ""}${dim ? " web-dim" : ""}`}
              >
                <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} class="web-line" />
                {/* A fat invisible line: easy to hit with a finger. */}
                <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} class="web-hit" onClick={open} />
                {(labels || chosen) && (
                  <text x={(p.x + q.x) / 2} y={(p.y + q.y) / 2} class="web-edge-text" text-anchor="middle" onClick={open}>
                    <title>{text}</title>
                    {short(text, 26)}
                  </text>
                )}
              </g>
            );
          })}
          {nodes.map((n) => {
            const at = layout.points.get(n.id);
            if (!at) return null;
            return (
              <g
                key={n.id}
                class={`web-node web-kind-${KIND_ORDER.includes(n.kind) ? n.kind : "other"}${n.id === center ? " centre" : ""}${n.revealed === false ? " web-dim" : ""}`}
                transform={`translate(${at.x} ${at.y})`}
                onClick={() => {
                  if (!dragged.current) onCenter(n.id);
                }}
              >
                <title>{n.name}</title>
                <Shape kind={n.kind} r={n.id === center ? SHAPE_R * 1.25 : SHAPE_R} />
                <text y={SHAPE_R * 1.25 + 18} text-anchor="middle" class="web-node-text">
                  {n.secret ? `${words.secretMark} ` : ""}
                  {short(n.name)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      <section aria-labelledby="web-legend" class="web-legend">
        <h3 id="web-legend" class="small">
          {words.legendHeading}
        </h3>
        <ul class="row small">
          {[...kindsHere, ...otherKinds].map((k) => (
            <li key={k}>
              <svg width="26" height="26" viewBox="-15 -15 30 30" aria-hidden="true" class={`web-kind-${KIND_ORDER.includes(k) ? k : "other"}`}>
                <Shape kind={k} r={9} />
              </svg>{" "}
              {kindWords(k)}
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

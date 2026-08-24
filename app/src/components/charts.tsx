// Chart primitives + the two shared interactions from the mockup:
//   • a fixed tooltip that shows a hovered point's data
//   • hovering a chart puts its tile into ".charting" (which suppresses the
//     explanation pop-out, so the two hovers never collide)
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

// ---- tooltip ----------------------------------------------------------------

interface TooltipApi {
  show: (clientX: number, clientY: number, main: string, sub?: string) => void;
  hide: () => void;
}
const TooltipContext = createContext<TooltipApi>({ show: () => {}, hide: () => {} });
export const useTooltip = () => useContext(TooltipContext);

export function TooltipProvider({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const show = useCallback((x: number, y: number, main: string, sub?: string) => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = main + (sub ? ` <small>${sub}</small>` : "");
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.classList.add("on");
  }, []);
  const hide = useCallback(() => ref.current?.classList.remove("on"), []);
  return (
    <TooltipContext.Provider value={{ show, hide }}>
      {children}
      <div ref={ref} className="tip" />
    </TooltipContext.Provider>
  );
}

// ---- per-tile "charting" flag ----------------------------------------------

const TileContext = createContext<(v: boolean) => void>(() => {});
export const TileChartingProvider = TileContext.Provider;
export const useSetCharting = () => useContext(TileContext);

// ---- geometry (ported from the mockup) -------------------------------------

function ptsOf(data: number[], W: number, H: number, pad = 4): [number, number][] {
  const mn = Math.min(...data);
  const mx = Math.max(...data);
  const r = mx - mn || 1;
  const n = Math.max(1, data.length - 1);
  return data.map((v, i) => [pad + (i * (W - 2 * pad)) / n, H - 5 - ((v - mn) / r) * (H - 10)]);
}

function smooth(P: [number, number][], t = 0.18): string {
  if (P.length < 2) return P.length ? `M ${P[0][0]},${P[0][1]}` : "";
  let d = `M ${P[0][0]},${P[0][1]}`;
  for (let i = 0; i < P.length - 1; i++) {
    const p0 = P[i - 1] || P[i];
    const p1 = P[i];
    const p2 = P[i + 1];
    const p3 = P[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) * t;
    const c1y = p1[1] + (p2[1] - p0[1]) * t;
    const c2x = p2[0] - (p3[0] - p1[0]) * t;
    const c2y = p2[1] - (p3[1] - p1[1]) * t;
    d += ` C ${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(
      2
    )},${p2[1].toFixed(2)}`;
  }
  return d;
}

function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 120, h: 34 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setSize({ w: Math.max(48, Math.round(r.width)), h: Math.max(20, Math.round(r.height)) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

// ---- Sparkline / line chart -------------------------------------------------

export function Sparkline({
  data,
  area = false,
  band = false,
  valueFmt,
  labelFor,
}: {
  data: number[];
  area?: boolean;
  band?: boolean;
  valueFmt: (v: number) => string;
  labelFor?: (i: number, n: number) => string;
}) {
  const [ref, size] = useSize<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement>(null);
  const markRef = useRef<SVGCircleElement>(null);
  const setCharting = useSetCharting();
  const { show, hide } = useTooltip();
  const gid = "g" + useId().replace(/:/g, "");

  if (!data.length)
    return <div ref={ref} className="chart-wrap" style={{ width: "100%", height: "100%" }} />;

  const W = size.w;
  const H = size.h;
  const P = ptsOf(data, W, H);
  const line = smooth(P);
  const last = P[P.length - 1];

  // baseline band (mean ± SD) for trend-style charts
  let bandRect: ReactNode = null;
  if (band && data.length > 2) {
    const mn = Math.min(...data);
    const mx = Math.max(...data);
    const r = mx - mn || 1;
    const mean = data.reduce((a, b) => a + b, 0) / data.length;
    const sd = Math.sqrt(data.reduce((a, b) => a + (b - mean) ** 2, 0) / data.length);
    const y = (v: number) => H - 5 - ((v - mn) / r) * (H - 10);
    const yT = y(mean + sd);
    const yB = y(mean - sd);
    bandRect = (
      <rect
        className="band"
        x={0}
        y={Math.min(yT, yB)}
        width={W}
        height={Math.abs(yB - yT) || 6}
        rx={6}
      />
    );
  }

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * (W / rect.width);
    let bi = 0;
    let bd = Infinity;
    for (let i = 0; i < P.length; i++) {
      const dd = Math.abs(P[i][0] - sx);
      if (dd < bd) {
        bd = dd;
        bi = i;
      }
    }
    const m = markRef.current;
    if (m) {
      m.setAttribute("cx", String(P[bi][0]));
      m.setAttribute("cy", String(P[bi][1]));
    }
    const pcx = rect.left + P[bi][0] * (rect.width / W);
    const pcy = rect.top + P[bi][1] * (rect.height / H);
    show(pcx, pcy, valueFmt(data[bi]), labelFor?.(bi, data.length));
  };

  return (
    <div ref={ref} className="chart-wrap" style={{ width: "100%", height: "100%" }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "100%", display: "block", overflow: "visible", cursor: "crosshair" }}
        onMouseEnter={() => setCharting(true)}
        onMouseLeave={() => {
          setCharting(false);
          markRef.current?.setAttribute("cx", "-99");
          hide();
        }}
        onMouseMove={onMove}
      >
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.38" />
            <stop offset="55%" stopColor="currentColor" stopOpacity="0.1" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {bandRect}
        {area && (
          <path
            className="area"
            d={`${line} L ${last[0].toFixed(2)},${H} L ${P[0][0].toFixed(2)},${H} Z`}
            fill={`url(#${gid})`}
            stroke="none"
          />
        )}
        <path className="line" pathLength={1} d={line} />
        <circle className="dotglow" cx={last[0]} cy={last[1]} r={area ? 6.5 : 5.5} />
        <circle className="dotend" cx={last[0]} cy={last[1]} r={area ? 3.4 : 3} />
        <circle ref={markRef} className="mk" cx={-99} cy={-99} r={4.5} />
      </svg>
    </div>
  );
}

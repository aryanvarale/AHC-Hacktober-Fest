import { useEffect, useRef, type ReactNode } from "react";

/**
 * A component's skeleton, drawn as a 3D field of dots, that morphs into the real component.
 *
 * 1. assemble: dots fly in from scatter and rise into a relief of the component's silhouette
 * 2. hold:     the relief turns slowly in perspective
 * 3. morph:    the relief flattens, a sweep fades the dots out while the real component resolves
 */

export type SkeletonKind = "StatusCard" | "DataTable" | "BarChart" | "LineChart" | "GeoMap" | "Heatmap" | null;

const MIN_GAP = 10;      // px between dots (grows for big components to cap the dot count)
const MAX_DOTS = 1500;
const AMP = 30;          // px a lit dot is raised
const PERSPECTIVE = 1100;
const PITCH = -0.5;      // look down on the field
const HOLD_MS = 950;     // time the relief is shown before the morph starts
const MORPH_MS = 900;

const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const hash = (a: number, b: number) => {
  const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
  return s - Math.floor(s);
};

/** Height (0..1) of the silhouette at normalised (u, v). */
function relief(kind: SkeletonKind, u: number, v: number, t: number): number {
  switch (kind) {
    case "BarChart": {
      const rows = 6, row = Math.floor(v * rows), f = v * rows - row;
      if (f < 0.18 || f > 0.82) return 0;
      if (u < 0.12) return 0.35;                       // label column
      return u < 0.16 + (0.78 - row * 0.1) * (0.92 - 0.04 * hash(row, 3)) ? 1 : 0;
    }
    case "DataTable": {
      if (v < 0.11) return 1;                          // header
      const row = (v - 0.11) / 0.145, f = row - Math.floor(row);
      if (f < 0.1) return 0.45;                        // row rule
      const col = Math.floor(u * 5), cf = u * 5 - col;
      return cf > 0.12 && cf < 0.12 + 0.55 * (0.5 + 0.5 * hash(Math.floor(row), col)) && f > 0.35 && f < 0.7 ? 0.8 : 0;
    }
    case "LineChart": {
      const y = 0.52 + 0.26 * Math.sin(u * 7 + 0.6) * Math.cos(u * 2.1) - 0.05;
      const d = Math.abs(v - y);
      if (d < 0.045) return 1;
      if (v > y && v < 0.92) return 0.18;              // area under the curve
      return v > 0.93 && v < 0.96 ? 0.4 : 0;           // axis
    }
    case "Heatmap": {
      const cols = 12, rows = 4, cu = u * cols, cv = v * rows;
      const fu = cu - Math.floor(cu), fv = cv - Math.floor(cv);
      if (fu < 0.12 || fv < 0.14) return 0;
      return 0.25 + 0.75 * hash(Math.floor(cu), Math.floor(cv));
    }
    case "GeoMap": {
      let h = u % 0.25 < 0.012 || v % 0.25 < 0.02 ? 0.12 : 0;
      for (let i = 0; i < 9; i++) {
        const px = 0.1 + 0.8 * hash(i, 1), py = 0.15 + 0.7 * hash(i, 2), r = 0.03 + 0.04 * hash(i, 3);
        const d = Math.hypot((u - px) * 2.2, v - py) / r;
        if (d < 1) h = Math.max(h, 1 - d * 0.55);       // cones
      }
      return h;
    }
    case "StatusCard": {
      if (v < 0.22) return u < 0.3 ? 1 : u < 0.62 ? 0.6 : 0;   // badge + name
      if (v > 0.34 && v < 0.74) { const f = u * 4 - Math.floor(u * 4); return f > 0.06 && f < 0.94 ? 0.75 : 0; }  // stat tiles
      return v > 0.86 && u < 0.5 ? 0.4 : 0;
    }
    default: {
      // waiting for the agent: a slow ripple
      const d = Math.hypot(u - 0.5, (v - 0.5) * 0.6);
      return 0.28 + 0.28 * Math.sin(d * 14 - t * 2.4);
    }
  }
}

interface Props {
  kind: SkeletonKind;
  children?: ReactNode;
  /** while false the skeleton stays and the children stay hidden */
  ready?: boolean;
  minHeight?: number;
  /** skip the skeleton (restored history) */
  instant?: boolean;
}

export function DotMorph({ kind, children, ready = true, minHeight = 150, instant = false }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const kindRef = useRef(kind);
  const readyRef = useRef(ready);
  const hasChildren = children != null;
  kindRef.current = kind;
  readyRef.current = ready && hasChildren;

  useEffect(() => {
    const el = wrap.current, cv = canvas.current, body = content.current;
    if (!el || !cv || !body) return;
    const ctx = cv.getContext("2d");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!ctx || reduced || instant) { body.style.cssText = ""; cv.style.display = "none"; return; }

    let w = 0, h = 0, cols = 0, rows = 0, GAP = MIN_GAP;
    let z: Float32Array, jx: Float32Array, jy: Float32Array, jz: Float32Array;
    const alloc = () => {
      GAP = Math.max(MIN_GAP, Math.sqrt((w * h) / MAX_DOTS));
      cols = Math.max(2, Math.floor(w / GAP)); rows = Math.max(2, Math.floor(h / GAP));
      const n = cols * rows;
      z = new Float32Array(n);
      jx = new Float32Array(n); jy = new Float32Array(n); jz = new Float32Array(n);
      for (let i = 0; i < n; i++) {   // scatter: where each dot starts
        jx[i] = (Math.random() - 0.5) * 520; jy[i] = (Math.random() - 0.5) * 320; jz[i] = (Math.random() - 0.5) * 420;
      }
    };
    const resize = () => {
      const r = el.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (Math.round(r.width) === w && Math.round(r.height) === h) return;
      w = Math.round(r.width); h = Math.round(r.height);
      cv.width = w * dpr; cv.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      alloc();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    const born = performance.now();
    let morphStart = 0, last = born, raf = 0, assemble = 0, done = false;

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = (now - born) / 1000;
      if (!morphStart && readyRef.current && now - born > HOLD_MS) morphStart = now;
      const p = morphStart ? clamp((now - morphStart) / MORPH_MS) : 0;
      assemble = Math.min(1, assemble + dt / 0.9);

      // content resolves from the middle of the morph
      const reveal = easeOut(clamp((p - 0.3) / 0.7));
      body.style.opacity = String(reveal);
      body.style.filter = reveal >= 1 ? "none" : `blur(${((1 - reveal) * 10).toFixed(1)}px)`;
      body.style.transform = reveal >= 1 ? "none" : `translateY(${((1 - reveal) * 6).toFixed(1)}px) scale(${(0.985 + 0.015 * reveal).toFixed(4)})`;

      ctx.clearRect(0, 0, w, h);
      const ease = easeInOut(p);
      const yaw = Math.sin(t * 0.7) * 0.3 * (1 - ease);
      const pitch = PITCH * (1 - ease);
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const k = kindRef.current;
      const smooth = 1 - Math.exp(-dt * 7);
      const fly = 1 - easeOut(assemble);

      for (let r = 0; r < rows; r++) {
        const v = (r + 0.5) / rows;
        for (let c = 0; c < cols; c++) {
          const i = r * cols + c, u = (c + 0.5) / cols;
          const hgt = relief(k, u, v, t);
          z[i] += (hgt - z[i]) * smooth;                       // dots rise / fall toward the silhouette
          const lit = z[i];

          const x0 = (c + 0.5) * GAP - (cols * GAP) / 2 + jx[i] * fly;
          const y0 = (r + 0.5) * GAP - (rows * GAP) / 2 + jy[i] * fly;
          const z0 = lit * AMP * (1 - ease) + jz[i] * fly;
          const x1 = x0 * cy + z0 * sy, z1 = -x0 * sy + z0 * cy;
          const y2 = y0 * cp - z1 * sp, z2 = y0 * sp + z1 * cp;
          const f = PERSPECTIVE / (PERSPECTIVE - z2);
          const sx = w / 2 + x1 * f, sY = h / 2 + y2 * f;
          if (sx < -4 || sx > w + 4 || sY < -4 || sY > h + 4) continue;

          // fade by a left-to-right sweep during the morph, leaving the content behind
          const sweep = clamp(1 - (p * 1.5 - u) / 0.35);
          let alpha = (0.07 + 0.8 * lit) * (0.55 + 0.45 * clamp(0.5 + z2 / 80)) * (1 - fly) * sweep;
          if (alpha < 0.012) continue;
          if (alpha > 1) alpha = 1;
          const g = clamp(lit * 1.2);
          ctx.fillStyle = `rgba(${Math.round(235 - 96 * g)},${Math.round(235 - 112 * g)},${Math.round(235 + 20 * g)},${alpha.toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(sx, sY, (0.9 + 0.9 * lit) * f, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      if (p >= 1 && !done) {
        done = true;
        cv.style.display = "none";
        body.style.cssText = "";
        return;
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [instant]);

  return (
    <div ref={wrap} className="relative" style={{ minHeight: hasChildren ? undefined : minHeight }}>
      <div ref={content} style={{ opacity: 0 }}>{children}</div>
      <canvas ref={canvas} className="pointer-events-none absolute inset-0 size-full" aria-hidden="true" />
    </div>
  );
}

import { useEffect, useRef } from "react";

/**
 * Orbi, the copilot's mascot: a glass orb with two pill eyes.
 *
 * The eyes never change shape. They only (a) follow the cursor when it comes near, (b) watch the text caret while
 * someone types, and (c) blink. Everything else is the body. Orbi works in a cycle, one thing at a time:
 *   action (at least 4.8 s)  ->  standing still and blinking for 8 s  ->  a different action  ->  ...
 * Actions: float, swirl, sparkle, bounce, wiggle. Clicking it does nothing.
 */

type Action = "float" | "swirl" | "sparkle" | "bounce" | "wiggle" | "idle";

const MIN_SECONDS = 4.8;
const IDLE_SECONDS = 8; // between two actions Orbi stands still and blinks
const CYCLE: Action[] = ["float", "swirl", "sparkle", "bounce", "wiggle"];

const NEAR = 380; // px: the cursor closer than this is "nearby" and Orbi looks at it

const clamp = (v: number, a = -1, b = 1) => Math.min(b, Math.max(a, v));
const ease = (t: number) => t * t * (3 - 2 * t);
const bell = (t: number) => Math.sin(Math.PI * clamp(t, 0, 1));
const TAU = Math.PI * 2;

/** A shuffled bag: every action is shown once, in random order, before any of them comes round again. */
let bag: Action[] = [];
function next(last: string): Action {
  if (bag.length === 0) {
    bag = [...CYCLE].sort(() => Math.random() - 0.5);
    if (bag[bag.length - 1] === last) bag.reverse();   // the one popped first must not repeat the last action
  }
  return bag.pop() as Action;
}

interface Frame { eye: number; dx: number; hop: number; sx: number; sy: number; rot: number; orbit: number; orbitAngle: number; sparkle: number }
const REST: Frame = { eye: 1, dx: 0, hop: 0, sx: 1, sy: 1, rot: 0, orbit: 0, orbitAngle: 0, sparkle: 0 };

function pose_idle(t: number): Partial<Frame> {
  const times = [1.1, 2.9, 3.2, 4.8, 6.4, 7.3];
  return { eye: 1 - Math.max(0, ...times.map((ti) => bell((t - ti) / 0.2))) };
}

/** pose of an action t seconds in; `dur` is how long this run lasts */
function pose(action: Action, t: number, dur: number): Partial<Frame> {
  // every action eases in and out so the hand-over from one to the next is smooth
  const env = ease(clamp(t / 0.45, 0, 1)) * ease(clamp((dur - t) / 0.45, 0, 1));
  if (action === "idle") return pose_idle(t);
  switch (action) {
    case "float": {
      // x swings once while y swings twice, which draws a figure eight: left-up, across, right-up, across
      const a = (t * TAU) / 5.2;
      return { dx: 11 * Math.sin(a) * env, hop: -env * (6 + 5 * Math.sin(2 * a)), rot: 1.6 * Math.cos(a) * env };
    }
    case "swirl": {
      const a = t * 2.7;
      return { orbit: env, orbitAngle: a, rot: 3 * Math.sin(a) * env };
    }
    case "sparkle":
      return { sparkle: env, hop: -2 * env * Math.sin(t * 1.6) };
    case "bounce": {
      const k = Math.sin((Math.PI * t) / 1.4) ** 2;
      return { hop: -9 * k * env, sy: 1 + 0.012 * k * env, sx: 1 - 0.008 * k * env };
    }
    case "wiggle":
      return { rot: 4 * Math.sin((t * TAU) / 1.6) * env };
  }
}

const STAR = "M0 -9 C1 -3 3 -1 9 0 C3 1 1 3 0 9 C-1 3 -3 1 -9 0 C-3 -1 -1 -3 0 -9Z";

export default function Mascot({ active }: { active: boolean }) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = root.current;
    if (!el || !active) return;
    const q = <T extends Element = SVGElement>(k: string) => el.querySelector<T>(`[data-m="${k}"]`)!;
    const body = q("body"), face = q("face"), halo = q("halo");
    const eyeL = q("eyeL"), eyeR = q("eyeR");
    const orbit = q("orbit"), sparkles = q("sparkles");
    const dots = Array.from(orbit.children) as SVGElement[];
    const stars = Array.from(sparkles.children) as SVGElement[];
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const measure = document.createElement("canvas").getContext("2d");
    const t0 = performance.now();
    const s = {
      gx: 0, gy: 0, now: t0,
      action: null as null | { name: Action; t0: number; dur: number },
      lastAction: "", wasTyping: false, nextBlink: t0 + 2500, blinkT0: -1e9,
      typingUntil: 0, caret: { x: 0, y: 0 },
      pointer: { x: -1e5, y: -1e5, t: 0 },
    };

    const start = (name: Action) => {
      const dur = name === "idle" ? IDLE_SECONDS : MIN_SECONDS + Math.random() * 1.4;
      s.action = { name, t0: s.now, dur };
      if (CYCLE.includes(name)) s.lastAction = name;
    };

    const onMove = (e: PointerEvent) => { s.pointer = { x: e.clientX, y: e.clientY, t: performance.now() }; };
    const onLeave = () => { s.pointer.x = -1e5; };
    const caretFor = (field: HTMLTextAreaElement) => {
      const r = field.getBoundingClientRect();
      let w = 0;
      if (measure) { measure.font = getComputedStyle(field).font; w = measure.measureText(field.value).width; }
      s.caret = { x: r.left + Math.min(w + 4, r.width - 30), y: r.top + r.height / 2 };
    };
    const isField = (t: EventTarget | null): t is HTMLTextAreaElement => (t as HTMLElement | null)?.classList?.contains("mo-field") ?? false;
    const onInput = (e: Event) => {
      if (!isField(e.target)) return;
      caretFor(e.target);
      s.typingUntil = performance.now() + 1600;
    };
    const onFocus = (e: FocusEvent) => {
      if (!isField(e.target)) return;
      caretFor(e.target);
      s.typingUntil = performance.now() + 1200;
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    document.addEventListener("input", onInput, true);
    document.addEventListener("focusin", onFocus);

    let raf = 0, last = t0;
    const frame = (now: number) => {
      // schedule first so an error in one frame can never end the animation for good
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now; s.now = now;
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height * 0.5;

      const typing = now < s.typingUntil;
      const near = Math.hypot(s.pointer.x - cx, s.pointer.y - cy) < NEAR && now - s.pointer.t < 6000;

      // typing pauses the cycle (Orbi watches you); when you stop it carries on at once
      if (typing && s.action) s.action = null;
      if (!typing && s.wasTyping && !s.action && !reduced) start("idle");
      s.wasTyping = typing;

      let f: Frame = { ...REST };
      if (s.action) {
        const t = (now - s.action.t0) / 1000;
        if (t >= s.action.dur) {
          const wasIdle = s.action.name === "idle";
          s.action = null;
          if (!typing && !reduced) start(wasIdle ? next(s.lastAction) : "idle"); // action -> 8 s of blinking -> next action
        } else f = { ...f, ...pose(s.action.name, t, s.action.dur) };
      } else if (!typing && !reduced && now - t0 > 400) {
        start(s.lastAction === "" ? "float" : "idle");
      }
      // while typing, or with reduced motion, there is no action: just an occasional blink
      if (!s.action) {
        if (now >= s.nextBlink) { s.blinkT0 = now; s.nextBlink = now + (reduced ? 4500 : 2600) + Math.random() * 2000; }
        const since = (now - s.blinkT0) / 1000;
        if (since < 0.2) f.eye = 1 - bell(since / 0.2);
      }

      // the eyes only follow the cursor (when it is near) or the text caret (while typing)
      let tx = 0, ty = 0;
      if (typing || near) {
        const px = typing ? s.caret.x : s.pointer.x, py = typing ? s.caret.y : s.pointer.y;
        const dx = px - cx, dy = py - cy, len = Math.hypot(dx, dy) || 1, mag = clamp(len / 140, 0, 1);
        tx = (dx / len) * mag; ty = (dy / len) * mag;
      }
      const k = 1 - Math.exp(-dt * (reduced ? 40 : 11));
      s.gx += (tx - s.gx) * k; s.gy += (ty - s.gy) * k;
      const lean = s.gx * 4;

      body.setAttribute("transform", `translate(${f.dx.toFixed(2)} ${f.hop.toFixed(2)}) rotate(${(f.rot + lean).toFixed(2)} 100 164) translate(100 164) scale(${f.sx.toFixed(3)} ${f.sy.toFixed(3)}) translate(-100 -164)`);
      face.setAttribute("transform", `translate(${(s.gx * 18).toFixed(2)} ${(s.gy * 13).toFixed(2)}) translate(100 100) scale(${(1 - Math.abs(s.gx) * 0.07).toFixed(3)} 1) translate(-100 -100)`);
      const eyeT = (cx0: number) => `translate(${cx0} 88) scale(1 ${Math.max(0.04, f.eye).toFixed(3)}) translate(${-cx0} -88)`;
      eyeL.setAttribute("transform", eyeT(82)); eyeR.setAttribute("transform", eyeT(118));

      halo.style.opacity = String((0.3 + f.sparkle * 0.15).toFixed(3));

      orbit.style.opacity = String(f.orbit.toFixed(3));
      if (f.orbit > 0.01) dots.forEach((d, i) => {
        const th = f.orbitAngle + (i / dots.length) * TAU, z = Math.sin(th);
        d.setAttribute("cx", (100 + 88 * Math.cos(th)).toFixed(1));
        d.setAttribute("cy", (104 + 24 * z - 6 * Math.cos(th)).toFixed(1));
        d.setAttribute("r", (2.1 + 1.1 * z).toFixed(2));
        d.style.opacity = String((0.3 + 0.7 * (z + 1) / 2).toFixed(2));
      });
      sparkles.style.opacity = f.sparkle > 0.01 ? "1" : "0";
      if (f.sparkle > 0.01) stars.forEach((st, i) => {
        const p = clamp(f.sparkle * 1.4 - i * 0.18, 0, 1);
        const [x, y] = [[150, 50], [46, 66], [158, 112]][i];
        st.setAttribute("transform", `translate(${x} ${y}) rotate(${(p * 60).toFixed(0)}) scale(${(p * (0.9 + 0.3 * Math.sin(now / 90 + i))).toFixed(3)})`);
      });
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("input", onInput, true);
      document.removeEventListener("focusin", onFocus);
    };
  }, [active]);

  return (
    <div ref={root} className="mo-mascot" data-active={active ? "" : undefined} aria-hidden="true">
      <svg viewBox="0 0 200 200" width="100%" height="100%" overflow="visible">
        <defs>
          {/* glass orb: violet at the top, deep indigo in the middle, cyan where it meets the glow below */}
          <linearGradient id="mo-body" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8a7dff" />
            <stop offset="38%" stopColor="#4d39f2" />
            <stop offset="72%" stopColor="#3f4cff" />
            <stop offset="100%" stopColor="#58d8ff" />
          </linearGradient>
          <radialGradient id="mo-glass" cx="50%" cy="50%" r="50%">
            <stop offset="72%" stopColor="#fff" stopOpacity="0" />
            <stop offset="100%" stopColor="#e9e6ff" stopOpacity="0.62" />
          </radialGradient>
          <radialGradient id="mo-bottom" cx="50%" cy="104%" r="62%">
            <stop offset="0%" stopColor="#7defff" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#7defff" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="mo-halo" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#6d5cff" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#6d5cff" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="mo-spec" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#fff" stopOpacity="0" />
          </radialGradient>
        </defs>

        <circle data-m="halo" cx="100" cy="100" r="98" fill="url(#mo-halo)" opacity="0.3" />

        <g data-m="body">
          <circle cx="100" cy="100" r="64" fill="url(#mo-body)" />
          <circle cx="100" cy="100" r="64" fill="url(#mo-bottom)" />
          <circle cx="100" cy="100" r="64" fill="url(#mo-glass)" />
          <ellipse cx="82" cy="62" rx="26" ry="11" fill="url(#mo-spec)" transform="rotate(-24 82 62)" />

          <g data-m="face">
            <g data-m="eyeL"><rect x="78" y="76.5" width="8" height="23" rx="4" fill="#fff" /></g>
            <g data-m="eyeR"><rect x="114" y="76.5" width="8" height="23" rx="4" fill="#fff" /></g>
          </g>
        </g>

        <g data-m="orbit" opacity="0" fill="#9be8ff">{Array.from({ length: 12 }).map((_, i) => <circle key={i} r="2.4" cx="100" cy="100" />)}</g>
        <g data-m="sparkles" opacity="0" fill="#d4dbff">{[0, 1, 2].map((i) => <path key={i} d={STAR} />)}</g>
      </svg>
      <p className="mo-mascot-hi">Hello! How can I help you today?</p>
    </div>
  );
}

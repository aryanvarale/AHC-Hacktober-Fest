import { useEffect, useMemo, useState, type ReactNode } from "react";
import { bound, fill, type Surface } from "@/lib/a2ui";
import { DotMorph, type SkeletonKind } from "@/components/a2ui/DotMorph";

export type OnAction = (name: string, context: Record<string, any>) => void;

interface Ctx { surface: Surface; onAction: OnAction; disabled: boolean; instant: boolean }

const card = "ui-card p-4";
const num = (v: unknown) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : String(v ?? ""));

/** Renders an A2UI surface using this app's component catalog. */
export function A2UIRenderer({ surface, onAction, disabled, instant = false }: { surface: Surface; onAction: OnAction; disabled: boolean; instant?: boolean }) {
  if (!surface.ready || !surface.root) return <div className="h-24 animate-pulse rounded-2xl bg-white/5" />;
  return <Node id={surface.root} ctx={{ surface, onAction, disabled, instant }} />;
}

function Node({ id, ctx }: { id: string; ctx: Ctx }): ReactNode {
  const comp = ctx.surface.components[id];
  if (!comp) return null;
  const { props } = comp;
  const data = ctx.surface.data;
  const kids = (props.children?.explicitList ?? []) as string[];

  switch (comp.type) {
    case "Column":
      return (
        <div className="flex flex-col gap-3">
          {kids.map((k, i) => (
            // the title lands at once; everything after the visual waits for its morph
            <div key={k} style={i > 1 && !ctx.instant ? { animation: "mo-in .45s ease-out both", animationDelay: "1.6s" } : undefined}>
              <Node id={k} ctx={ctx} />
            </div>
          ))}
        </div>
      );
    case "Row":
      return <div className={`flex gap-2 ${props.wrap ? "flex-wrap" : ""}`}>{kids.map((k) => <Node key={k} id={k} ctx={ctx} />)}</div>;
    case "Text": {
      const t = bound<string>(props.text, data, "");
      if (props.usageHint === "h2") return <h3 className="text-[15px] font-semibold text-ink">{t}</h3>;
      if (props.usageHint === "caption") return <p className="text-xs text-ink-3">{t}</p>;
      return <p className="text-sm text-ink-2">{t}</p>;
    }
    case "Button": {
      const label = bound<string>(ctx.surface.components[props.child]?.props.text, data, "");
      const q = props.action?.context?.question as string | undefined;
      return (
        <button
          type="button"
          disabled={ctx.disabled}
          onClick={() => q && ctx.onAction(props.action.name, props.action.context)}
          className="ui-pill" data-tone={props.primary ? "accent" : undefined}
        >
          {label}
        </button>
      );
    }
    case "StatusCard": return <Morph kind="StatusCard" instant={ctx.instant}><StatusCard s={bound(props.data, data, {})} /></Morph>;
    case "CompactList": return <CompactList props={props} ctx={ctx} />;
    case "TemperatureCard": return <TemperatureCard t={bound(props.data, data, {})} />;
    case "SpillList": return <SpillList s={bound(props.data, data, {})} />;
    case "RuleList": return <RuleList packet={bound<{ groups?: { heading: string; items: Record<string, any>[] }[] }>(props.data, data, {})} />;
    case "LateList": return <LateList packet={bound(props.data, data, {})} />;
    case "ActionList": return <ActionList props={props} ctx={ctx} />;
    case "DataTable": return <Morph kind="DataTable" instant={ctx.instant}><DataTable props={props} ctx={ctx} /></Morph>;
    case "BarChart": return <Morph kind="BarChart" instant={ctx.instant}><BarChart props={props} ctx={ctx} /></Morph>;
    case "LineChart": return <Morph kind="LineChart" instant={ctx.instant}><LineChart props={props} ctx={ctx} /></Morph>;
    case "GeoMap": return <Morph kind="GeoMap" instant={ctx.instant}><GeoMap props={props} ctx={ctx} /></Morph>;
    case "Heatmap": return <Morph kind="Heatmap" instant={ctx.instant}><Heatmap props={props} ctx={ctx} /></Morph>;
    case "Evidence": return <Evidence ids={bound<string[]>(props.ids, data, [])} tool={props.tool ?? ""} ctx={ctx} />;
    default: return <p className="text-xs text-ink-3">Unsupported component: {comp.type}</p>;
  }
}

/* ───────────────────────────── status card ───────────────────────────── */

function StatusCard({ s }: { s: Record<string, any> }) {
  const down = !s.producing;
  return (
    <div className={card}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${down ? "bg-red-500/15 text-red-300" : "bg-emerald-400/15 text-emerald-300"}`}>
          <span className={`size-1.5 rounded-full ${down ? "bg-red-400" : "bg-emerald-400"} ${down ? "" : "animate-pulse"}`} />
          {down ? "Not producing" : "Producing"}
        </span>
        <span className="text-sm font-medium text-ink">{s.asset_id} · {s.well_name}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <span className="text-ink-3">Temperature <strong className="font-medium text-ink">{num(s.delta_c)}°C above air</strong> · needs {num(s.producing_delta_c)}°C</span>
        <span className="text-ink-3">Checked <strong className="font-medium text-ink">{String(s.inspected_at).slice(0, 10)}</strong></span>
        {s.recorded_status_stale && <span className="text-amber-300">Recorded status is out of date</span>}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-ink-2">{s.reason}</p>
      <p className="mt-2 text-[11px] text-ink-3">{s.inspection_id} · {s.inspector_name}</p>
    </div>
  );
}

/* ───────────────────────────── temperature ───────────────────────────── */

function TemperatureCard({ t }: { t: Record<string, any> }) {
  const bad = t.state !== "normal";
  const latest = t.latest ?? {};
  const since = t.abnormal_since;
  const lastNormal = t.last_normal;
  return (
    <div className={card}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${bad ? "bg-red-500/15 text-red-300" : "bg-emerald-400/15 text-emerald-300"}`}>
          {bad ? (t.state === "too hot" ? "Above normal" : "Below normal") : "Normal"}
        </span>
        <span className="text-sm font-medium text-ink">{t.asset_id} · {t.well_name}</span>
      </div>
      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-[150px_1fr]">
        <dt className="text-ink-3">Last reading</dt>
        <dd className="text-ink">{latest.wellhead_c} °C on {latest.at} <span className="text-ink-3">({latest.above_air_c} °C above air)</span></dd>
        <dt className="text-ink-3">Normal range</dt>
        <dd className="text-ink-2">{t.normal_range}</dd>
        {bad && since && (<>
          <dt className="text-ink-3">Abnormal since</dt>
          <dd className="font-medium text-red-300">{since.at} <span className="font-normal text-ink-3">({t.readings_out_of_range} inspection{t.readings_out_of_range === 1 ? "" : "s"} in a row)</span></dd>
        </>)}
        {bad && lastNormal && (<>
          <dt className="text-ink-3">Last normal reading</dt>
          <dd className="text-ink-2">{lastNormal.wellhead_c} °C on {lastNormal.at}</dd>
        </>)}
        {bad && t.cause && (<>
          <dt className="text-ink-3">Recorded reason</dt>
          <dd className="text-ink-2">{t.cause}</dd>
        </>)}
      </dl>
    </div>
  );
}

/* ───────────────────────────── rules ───────────────────────────── */

const ruleTone: Record<string, string> = {
  open: "bg-red-500/15 text-red-300",
  closed: "bg-white/8 text-ink-3",
  pending: "bg-amber-400/15 text-amber-200",
  met: "bg-emerald-400/15 text-emerald-300",
};
const ruleLabel: Record<string, string> = { open: "Still open", closed: "Closed", pending: "Pending", met: "Met" };

function RuleList({ packet }: { packet: { groups?: { heading: string; items: Record<string, any>[] }[] } }) {
  const shown = (packet.groups ?? []).filter((group) => group.items?.length);
  return (
    <div className="flex flex-col gap-3">
      {shown.map((group) => (
        <div key={group.heading} className={card}>
          <div className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{group.heading}</div>
          <div className="mt-2 divide-y divide-line">
            {group.items.map((item, i) => (
              <div key={i} className="grid grid-cols-[4.5rem_1fr_auto] items-start gap-3 py-2.5">
                <span className="text-sm font-medium text-ink">{item.rule}</span>
                <div className="min-w-0">
                  <p className="text-xs leading-relaxed text-ink-2">{item.detail}</p>
                  {Array.isArray(item.evidence) && item.evidence.length > 0 && (
                    <p className="mt-1 text-[11px] text-ink-3">{item.evidence.join(" · ")}</p>
                  )}
                </div>
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${ruleTone[item.status] ?? ruleTone.closed}`}>{ruleLabel[item.status] ?? item.status}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────────── spills ───────────────────────────── */

const spillTone: Record<string, string> = {
  Minor: "bg-white/8 text-ink-2",
  Moderate: "bg-amber-400/15 text-amber-200",
  Reportable: "bg-red-500/15 text-red-300",
  Major: "bg-red-500/15 text-red-300",
};

function LateList({ packet }: { packet: Record<string, any> }) {
  const items = (packet.items ?? []) as Record<string, any>[];
  return (
    <div className={card}>
      <p className="text-sm text-ink-2">{packet.summary}</p>
      <div className="mt-3 divide-y divide-line">
        {items.map((item) => (
          <div key={item.inspection_id} className="grid grid-cols-[1fr_auto] gap-3 py-2.5">
            <div>
              <div className="text-sm font-medium text-ink">{item.asset_id} <span className="font-normal text-ink-3">· {item.well_name}</span></div>
              <p className="mt-1 text-xs leading-relaxed text-ink-2">{item.detail}</p>
              <p className="mt-1 text-[11px] text-ink-3">{item.maintenance_id} · {item.inspection_id}</p>
            </div>
            <div className="text-right text-sm font-medium text-ink">{num(item.hours)} h</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SpillList({ s }: { s: Record<string, any> }) {
  const items = (s.items ?? []) as Record<string, string>[];
  return (
    <div className={card}>
      <p className="text-sm text-ink-2">{s.summary}</p>
      {items.length > 0 && (
        <div className="mt-3 divide-y divide-line">
          {items.map((item) => (
            <div key={item.inspection_id} className="grid grid-cols-[6.5rem_1fr_6.5rem_auto] items-center gap-3 py-2.5">
              <span className="text-sm font-medium text-ink">{item.when}</span>
              <span className="text-[11px] text-ink-3">{item.inspection_id}</span>
              <span className="text-right text-sm text-ink">{item.volume}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] ${spillTone[item.spill_class] ?? spillTone.Minor}`}>{item.spill_class}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────── concise list ───────────────────────────── */

function CompactList({ props, ctx }: { props: any; ctx: Ctx }) {
  const items = bound<Record<string, any>[]>(props.items, ctx.surface.data, []);
  const total = bound<number>(props.total, ctx.surface.data, items.length);
  return (
    <div className={`${card} divide-y divide-line py-1`}>
      {items.map((item, i) => (
        <button key={i} type="button" disabled={!item.question || ctx.disabled}
          onClick={() => item.question && ctx.onAction("ask", { question: item.question })}
          className="flex w-full items-start gap-3 py-3 text-left enabled:cursor-pointer enabled:hover:bg-white/[0.03]">
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <strong className="text-sm font-medium text-ink">{item.title}</strong>
              {item.status && <span className="rounded-full bg-white/7 px-2 py-0.5 text-[11px] text-ink-2">{item.status}</span>}
            </span>
            {item.detail && <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-ink-2">{item.detail}</span>}
            {item.meta && <span className="mt-1 block text-[11px] text-ink-3">{item.meta}</span>}
          </span>
          {item.question && <span className="pt-1 text-ink-3">›</span>}
        </button>
      ))}
      {total > items.length && <p className="py-2 text-[11px] text-ink-3">Showing {items.length} of {total}. Ask for a table to see all.</p>}
    </div>
  );
}

function ActionList({ props, ctx }: { props: any; ctx: Ctx }) {
  const actions = bound<Record<string, any>[]>(props.actions, ctx.surface.data, []);
  const [phase, setPhase] = useState<Record<string, "sending" | "sent" | "error">>({});
  const [note, setNote] = useState<Record<string, string>>({});

  const run = async (action: Record<string, any>) => {
    const id = String(action.id);
    if (phase[id] === "sending" || phase[id] === "sent") return;
    setPhase((current) => ({ ...current, [id]: "sending" }));
    try {
      const res = await fetch("/api/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: id, asset_id: action.asset_id ?? null }),
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "The agent did not accept the action.");
      setPhase((current) => ({ ...current, [id]: "sent" }));
      setNote((current) => ({ ...current, [id]: data.message }));
    } catch (err) {
      setPhase((current) => ({ ...current, [id]: "error" }));
      setNote((current) => ({ ...current, [id]: err instanceof Error ? err.message : "Could not reach that agent." }));
    }
  };

  return (
    <div className={card}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Suggested actions</div>
      <div className="mt-1 divide-y divide-line">
        {actions.map((action) => {
          const id = String(action.id);
          const state = phase[id];
          return (
            <div key={id} className="flex items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">{action.label}</div>
                <div className="mt-0.5 text-[11px] text-ink-3">{action.sop} · {action.detail}</div>
                {note[id] && <div className={`mt-1 text-[11px] ${state === "error" ? "text-red-300" : "text-emerald-300"}`}>{note[id]}</div>}
              </div>
              <button type="button" disabled={ctx.disabled || state === "sending" || state === "sent"} onClick={() => void run(action)}
                className="ui-pill shrink-0" data-tone={state === "sent" ? undefined : "accent"}>
                {state === "sending" ? "Sending" : state === "sent" ? "Sent" : state === "error" ? "Retry" : action.verb}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─────────────────────────────── table ─────────────────────────────── */

function DataTable({ props, ctx }: { props: any; ctx: Ctx }) {
  const rows = bound<Record<string, any>[]>(props.rows, ctx.surface.data, []);
  const columns = props.columns as { key: string; label: string }[];
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    return [...rows].sort((a, b) => (a[sort.key] > b[sort.key] ? 1 : a[sort.key] < b[sort.key] ? -1 : 0) * sort.dir);
  }, [rows, sort]);
  const act = props.rowAction;

  return (
    <div className={`${card} overflow-hidden p-0`}>
      <div className="max-h-80 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-raised text-ink-3">
            <tr>
              {columns.map((c) => (
                <th key={c.key} className="cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium capitalize hover:text-ink"
                  onClick={() => setSort((s) => ({ key: c.key, dir: s?.key === c.key && s.dir === 1 ? -1 : 1 }))}>
                  {c.label} {sort?.key === c.key ? (sort.dir === 1 ? "↑" : "↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}
                onClick={() => act && !ctx.disabled && ctx.onAction(act.name, { question: fill(act.template, r) })}
                className={`border-t border-line ${act ? "cursor-pointer hover:bg-white/5" : ""}`}>
                {columns.map((c) => (
                  <td key={c.key} className="max-w-[260px] truncate px-3 py-2 text-ink-2" title={String(r[c.key] ?? "")}>
                    {typeof r[c.key] === "boolean" ? (r[c.key] ? "yes" : "no") : c.key.endsWith("_at") || c.key.endsWith("_date") ? String(r[c.key]).slice(0, 10) : num(r[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t border-line px-3 py-1.5 text-[11px] text-ink-3">
        {rows.length} rows{act ? " · click a row to ask about it" : ""}
      </div>
    </div>
  );
}

/* ───────────────────────────── bar chart ───────────────────────────── */

function BarChart({ props, ctx }: { props: any; ctx: Ctx }) {
  const items = bound<{ label: string; value: number }[]>(props.items, ctx.surface.data, []).slice(0, 14);
  const max = Math.max(1, ...items.map((i) => i.value));
  const [shown, setShown] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { const t = requestAnimationFrame(() => setShown(true)); return () => cancelAnimationFrame(t); }, []);
  const act = props.itemAction;

  return (
    <div className={card}>
      <div className="flex flex-col gap-2">
        {items.map((it, i) => (
          <button key={i} type="button" disabled={!act || ctx.disabled}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            onClick={() => act && ctx.onAction(act.name, { question: fill(act.template, it) })}
            className="grid grid-cols-[88px_1fr_auto] items-center gap-3 text-left text-xs enabled:cursor-pointer">
            <span className={`truncate ${hover === i ? "text-ink" : "text-ink-3"}`}>{it.label}</span>
            <span className="h-5 rounded-md bg-white/5">
              <span className="block h-full rounded-md bg-gradient-to-r from-indigo-500/80 to-cyan-300 transition-[width] duration-700 ease-out"
                style={{ width: shown ? `${(it.value / max) * 100}%` : "0%", transitionDelay: `${i * 50}ms`, opacity: hover == null || hover === i ? 1 : 0.45 }} />
            </span>
            <span className="whitespace-nowrap text-right font-medium text-ink">{num(it.value)} <span className="text-ink-3">{props.unit}</span></span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────── line chart ───────────────────────────── */

function LineChart({ props, ctx }: { props: any; ctx: Ctx }) {
  const pts = bound<{ x: string; a: number; b: number | null; flag: boolean }[]>(props.points, ctx.surface.data, []);
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 230, L = 40, R = 12, T = 12, B = 28;
  const vals = pts.flatMap((p) => [p.a, p.b]).filter((v): v is number => typeof v === "number");
  const lo = Math.min(...vals, 0), hi = Math.max(...vals, 1);
  const x = (i: number) => L + (pts.length < 2 ? 0.5 : i / (pts.length - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
  const path = (k: "a" | "b") => pts.map((p, i) => (p[k] == null ? "" : `${i && pts[i - 1][k] != null ? "L" : "M"}${x(i).toFixed(1)},${y(p[k] as number).toFixed(1)}`)).join(" ");
  const ticks = [lo, lo + (hi - lo) / 2, hi];
  const h = hover != null ? pts[hover] : null;

  return (
    <div className={card}>
      <div className="mb-2 flex items-center justify-between text-xs">
        <div className="flex gap-3 text-ink-3">
          <span className="flex items-center gap-1.5"><i className="h-0.5 w-4 bg-[#8b7bff]" />{props.aLabel}</span>
          {props.bLabel && <span className="flex items-center gap-1.5"><i className="h-0.5 w-4 bg-sky-400" />{props.bLabel}</span>}
          <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-red-400" />not producing</span>
        </div>
        <span className="text-ink-2">{h ? `${h.x} · ${num(h.a)}${h.b != null ? ` / ${num(h.b)}` : ""}` : "hover a point"}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setHover(null)}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="white" strokeOpacity={0.08} />
            <text x={L - 6} y={y(t) + 3} textAnchor="end" fontSize={10} fill="white" fillOpacity={0.4}>{num(t)}</text>
          </g>
        ))}
        {props.bLabel && <path d={path("b")} fill="none" stroke="#38bdf8" strokeWidth={1.8} pathLength={1} className="mo-draw" />}
        <path d={path("a")} fill="none" stroke="#8b7bff" strokeWidth={2.2} pathLength={1} className="mo-draw" />
        {pts.map((p, i) => (
          <g key={i} onMouseEnter={() => setHover(i)}>
            <rect x={x(i) - 14} y={T} width={28} height={H - T - B} fill="transparent" />
            <circle cx={x(i)} cy={y(p.a)} r={hover === i ? 5 : p.flag ? 4 : 2.5} fill={p.flag ? "#f87171" : "#8b7bff"} />
          </g>
        ))}
        {pts.map((p, i) => (i % Math.ceil(pts.length / 6) === 0 ? <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill="white" fillOpacity={0.4}>{p.x.slice(5)}</text> : null))}
      </svg>
    </div>
  );
}

/* ─────────────────────────────── spill map ─────────────────────────────── */

function GeoMap({ props, ctx }: { props: any; ctx: Ctx }) {
  const pins = bound<Record<string, any>[]>(props.pins, ctx.surface.data, []);
  const [hover, setHover] = useState<number | null>(null);
  if (!pins.length) return <div className={card}><p className="text-xs text-ink-3">No spills in this period.</p></div>;
  const W = 640, H = 300, P = 28;
  const lats = pins.map((p) => p.lat), lons = pins.map((p) => p.lon);
  const [la0, la1, lo0, lo1] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
  const X = (lon: number) => P + ((lon - lo0) / (lo1 - lo0 || 1)) * (W - 2 * P);
  const Y = (lat: number) => H - P - ((lat - la0) / (la1 - la0 || 1)) * (H - 2 * P);
  const maxV = Math.max(1, ...pins.map((p) => p.spill_est_volume_bbl ?? 1));
  const h = hover != null ? pins[hover] : null;
  const act = props.itemAction;
  const total = pins.reduce((sum, p) => sum + (p.spill_est_volume_bbl ?? 0), 0);
  const tipLeft = h ? (X(h.lon) / W) * 100 : 0;
  const tipTop = h ? (Y(h.lat) / H) * 100 : 0;

  return (
    <div className={card}>
      <div className="mb-2 text-xs text-ink-3">
        {pins.length} well{pins.length === 1 ? "" : "s"} with a spill · {num(total)} barrels in total · larger circle means more barrels
      </div>
      <div className="relative" onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-xl bg-[#0d1216]">
          {[0.25, 0.5, 0.75].map((f) => (
            <g key={f} stroke="white" strokeOpacity={0.05}>
              <line x1={P + f * (W - 2 * P)} x2={P + f * (W - 2 * P)} y1={P} y2={H - P} />
              <line y1={P + f * (H - 2 * P)} y2={P + f * (H - 2 * P)} x1={P} x2={W - P} />
            </g>
          ))}
          {pins.map((p, i) => {
            const r = 5 + ((p.spill_est_volume_bbl ?? 1) / maxV) * 11;
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                className={act && !ctx.disabled ? "cursor-pointer outline-none" : "outline-none"}
                onClick={() => act && !ctx.disabled && ctx.onAction(act.name, { question: fill(act.template, p) })}>
                <circle cx={X(p.lon)} cy={Y(p.lat)} r={r + 6} fill="#f87171" opacity={hover === i ? 0.25 : 0.1} />
                <circle cx={X(p.lon)} cy={Y(p.lat)} r={r} fill="#f87171" fillOpacity={0.7} stroke="#fecaca" strokeWidth={hover === i ? 2 : 0.8} />
                <text x={X(p.lon)} y={Y(p.lat) + r + 14} textAnchor="middle" fontSize={11} fill="white" fillOpacity={hover === i ? 1 : 0.7}>{p.asset_id}</text>
              </g>
            );
          })}
        </svg>
        {h && (
          <div className="pointer-events-none absolute z-10 w-max min-w-[170px] -translate-x-1/2 rounded-xl border border-line-strong bg-raised/95 px-3 py-2 text-xs shadow-xl"
            style={{ left: `${Math.min(85, Math.max(15, tipLeft))}%`, top: `${tipTop}%`, transform: tipTop > 45 ? "translate(-50%, calc(-100% - 22px))" : "translate(-50%, 22px)" }}>
            <div className="font-medium text-ink">{h.asset_id}{h.well_name ? ` · ${h.well_name}` : ""}</div>
            <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-ink-2">
              <span className="text-ink-3">Date</span><span>{h.when ?? String(h.spill_date).slice(0, 10)}</span>
              <span className="text-ink-3">Barrels</span><span>{num(h.spill_est_volume_bbl)}</span>
              {h.spill_class && (<><span className="text-ink-3">Class</span><span>{h.spill_class}</span></>)}
              <span className="text-ink-3">Record</span><span>{h.inspection_id}</span>
            </div>
          </div>
        )}
      </div>
      <div className="mt-3 divide-y divide-line">
        {pins.map((p, i) => (
          <button key={i} type="button" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            disabled={!act || ctx.disabled}
            onClick={() => act && ctx.onAction(act.name, { question: fill(act.template, p) })}
            className={`grid w-full grid-cols-[1fr_auto_auto] items-center gap-3 py-2 text-left text-xs enabled:cursor-pointer ${hover === i ? "bg-white/[0.04]" : ""}`}>
            <span><span className="font-medium text-ink">{p.asset_id}</span>{p.well_name && <span className="text-ink-3"> · {p.well_name}</span>}</span>
            <span className="text-ink-2">{p.when ?? String(p.spill_date).slice(0, 10)}</span>
            <span className="text-right font-medium text-ink">{num(p.spill_est_volume_bbl)} barrels</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────── heatmap ─────────────────────────────── */

function Heatmap({ props, ctx }: { props: any; ctx: Ctx }) {
  const cells = bound<{ area_name: string; month: string | number; spills: number }[]>(props.cells, ctx.surface.data, []);
  const [hover, setHover] = useState<(typeof cells)[number] | null>(null);
  const areas = [...new Set(cells.map((c) => c.area_name))];
  const months = [...new Set(cells.map((c) => String(c.month)))].sort();
  const max = Math.max(1, ...cells.map((c) => c.spills));
  const at = (a: string, m: string) => cells.find((c) => c.area_name === a && String(c.month) === m);

  return (
    <div className={card}>
      <div className="mb-2 text-xs text-ink-2">{hover ? `${hover.area_name} · ${hover.month} · ${hover.spills} spills` : "hover a cell"}</div>
      <div className="overflow-x-auto">
        <div className="grid gap-1" style={{ gridTemplateColumns: `150px repeat(${months.length}, minmax(26px, 1fr))` }} onMouseLeave={() => setHover(null)}>
          <span />
          {months.map((m) => <span key={m} className="text-center text-[10px] text-ink-3">{m.slice(-2)}</span>)}
          {areas.map((a) => (
            <div key={a} className="contents">
              <span className="truncate pr-2 text-xs text-ink-3">{a}</span>
              {months.map((m) => {
                const c = at(a, m);
                const v = c?.spills ?? 0;
                return (
                  <span key={m} onMouseEnter={() => c && setHover(c)}
                    className="flex h-8 items-center justify-center rounded-md text-[10px] text-ink-2 transition hover:ring-1 hover:ring-white/60"
                    style={{ background: v ? `rgba(248,113,113,${0.12 + (v / max) * 0.8})` : "rgba(255,255,255,0.04)" }}>
                    {v || ""}
                  </span>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────── evidence ─────────────────────────────── */

function Evidence({ ids, tool, ctx }: { ids: string[]; tool: string; ctx: Ctx }) {
  const [all, setAll] = useState(false);
  if (!ids.length) return null;
  if (!all) {
    return (
      <button type="button" onClick={() => setAll(true)} className="w-fit text-[11px] text-ink-3 hover:text-ink-2">
        View {ids.length} source{ids.length === 1 ? "" : "s"}
      </button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      <span className="text-ink-3">Sources</span>
      {ids.map((id) => (
        <button key={id} type="button" onClick={() => ctx.onAction("evidence", { id, ids, tool })}
          title="Open the source record" className="ui-chip border border-transparent transition hover:border-accent/40 hover:bg-accent/10 hover:text-ink">{id}</button>
      ))}
      <button type="button" onClick={() => setAll(false)} className="text-[#cfc8ff] hover:underline">hide</button>
    </div>
  );
}

/** Every generated visual first appears as a dotted 3D skeleton, then morphs into the real component. */
function Morph({ kind, children, instant }: { kind: SkeletonKind; children: ReactNode; instant: boolean }) {
  return <DotMorph kind={kind} instant={instant}>{children}</DotMorph>;
}

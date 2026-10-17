"use client";

import { useMemo, useState, type ReactNode } from "react";

import { type Axis, minuteAt, toAxis } from "@/components/match-centre/replay";
import { InfoTip } from "@/components/ui/info-tip";
import type { Analytics } from "@/lib/api";
import { METRICS, TEAM_FIELD, type MetricKey } from "@/lib/metrics";
import { minuteLabel } from "@/lib/utils";

export interface Side {
  team_id: string;
  short_name: string;
  color: string;
}

/** A figure an aired insight carries, placed on the replay clock (for the speed metric). */
export interface InsightPoint {
  t: number;
  team_id: string | null;
  v: number;
  label: string;
}

interface Point {
  s: number; // replay clock (s)
  minute: number;
  v: number;
}

interface Series {
  id: string;
  label: string;
  color: string;
  points: Point[];
  kind: "line" | "step" | "dots";
}

const W = 600;
const H = 400; // the shot map's proportions (111 x 74), so the two sit as a matched pair
const M = { top: 16, right: 44, bottom: 24, left: 34 };
const TICKS = [0, 15, 30, 45, 60, 75, 90];
const CHAOS_COLOR = "#475569";
const STEPS = [0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50];
const FLOOR: Record<MetricKey, number> = {
  control: 100, possession: 100, pass_accuracy: 100, chaos: 100, tempo: 10, ppda: 10, xg: 1, speed: 40,
};
const NOTES: Record<MetricKey, string> = {
  control: "Rolling five-minute window, computed by the engine. ▼ marks an aired momentum insight.",
  possession: "Share of the last five minutes each team had the ball.",
  pass_accuracy: "Share of passes completed over the last five minutes.",
  tempo: "Passes per minute on the ball over the last five minutes.",
  ppda: "Opponent passes allowed per defensive action over the last five minutes: lower means more pressing.",
  chaos: "One line for the whole match: possession changes, play under pressure and duels over the last five minutes.",
  xg: "Each team's xG so far, rising with every shot.",
  speed: "Speeds from aired badges and insights: top running speeds and ball speeds of shots and passes.",
};

function niceTop(max: number, floor: number): { top: number; step: number } {
  const target = Math.max(max, floor);
  const step = STEPS.find((s) => target / s <= 4) ?? 100;
  return { top: Math.ceil(target / step) * step, step };
}

const fmt = (v: number, metric: MetricKey) => (metric === "xg" ? v.toFixed(2) : String(Math.round(v * 10) / 10));

/**
 * The metric the viewer chose, drawn up to the replay clock: one thin line per team (one for the
 * whole match for chaos), a crosshair readout, and the current values labelled at the line ends.
 * Every value comes from the API; nothing is computed here but positions.
 */
export function MetricChart({
  axis,
  t,
  metric,
  analytics,
  sides,
  markers,
  insightPoints,
  picker,
}: {
  axis: Axis;
  t: number;
  metric: MetricKey;
  analytics: Analytics;
  sides: Side[];
  markers: { t: number; color: string; label: string }[];
  insightPoints: InsightPoint[];
  picker?: ReactNode; // the metric selector, at the start of the header
}) {
  const def = METRICS[metric];
  const [hover, setHover] = useState<number | null>(null);
  const series = useMemo((): Series[] => {
    const at = (period: number, minute: number) => toAxis(axis, period, (minute - (period === 2 ? 45 : 0)) * 60000);
    if (def.source === "match") {
      const points = analytics.chaos.map((c) => ({ s: at(c.period, c.minute), minute: c.minute, v: c.chaos }));
      return [{ id: "chaos", label: "Match", color: CHAOS_COLOR, points, kind: "line" }];
    }
    return sides.map((side) => {
      let points: Point[] = [];
      let kind: Series["kind"] = "line";
      if (def.source === "team") {
        const field = TEAM_FIELD[metric as keyof typeof TEAM_FIELD];
        points = analytics.team_state
          .filter((r) => r.team_id === side.team_id && r[field] !== null)
          .map((r) => ({ s: at(r.period, r.minute), minute: r.minute, v: r[field] as number }));
      } else if (def.source === "shots") {
        kind = "step";
        points = [{ s: 0, minute: 0, v: 0 }];
        for (const shot of analytics.shots.filter((x) => x.team_id === side.team_id)) {
          const s = toAxis(axis, shot.period, shot.clock_ms);
          points.push({ s, minute: (shot.period === 2 ? 45 : 0) + shot.clock_ms / 60000, v: shot.team_xg });
        }
      } else {
        kind = "dots";
        points = insightPoints
          .filter((p) => p.team_id === side.team_id)
          .map((p) => ({ s: p.t, minute: 0, v: p.v }));
      }
      return { id: side.team_id, label: side.short_name, color: side.color, points, kind };
    });
  }, [analytics, sides, axis, metric, def.source, insightPoints]);

  const visible = series.map((s) => {
    const points = s.points.filter((p) => p.s <= t);
    const last = points.at(-1);
    if (s.kind === "step" && last && last.s < t) points.push({ ...last, s: t }); // flat until the next shot
    return { ...s, points };
  });
  const all = visible.flatMap((s) => s.points.map((p) => p.v));
  const { top, step } = def.max !== null ? { top: def.max, step: 25 } : niceTop(Math.max(0, ...all), FLOOR[metric]);
  const x = (s: number) => M.left + (s / axis.total) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - v / top) * (H - M.top - M.bottom);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);

  // Readout: at the hovered minute, or now. Lines read the latest point at or before it; for
  // speed, the fastest so far.
  const readAt = hover ?? t;
  const readout = visible.map((s) => {
    const before = s.points.filter((p) => p.s <= readAt + 0.5);
    if (s.kind === "dots") return before.length ? Math.max(...before.map((p) => p.v)) : undefined;
    return before.at(-1)?.v;
  });
  const readMinute = minuteLabel(minuteAt(axis, readAt));

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const s = ((((e.clientX - box.left) / box.width) * W - M.left) / (W - M.left - M.right)) * axis.total;
    setHover(s >= 0 && s <= t ? s : null);
  };

  const ends = visible.map((s) => s.points.at(-1));

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex min-h-9 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
        {picker}
        <span className="flex items-center gap-1 font-medium text-slate-900">
          {def.label}
          {def.source === "insights" ? " (fastest so far)" : ""} at {readMinute}
          <InfoTip term={def.term} align="start" />
        </span>
        {visible.map((s, i) => (
          <span key={s.id} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />
            {s.label}
            <strong className="font-mono text-slate-900">
              {readout[i] !== undefined ? `${fmt(readout[i], metric)}${def.unit}` : "–"}
            </strong>
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full touch-none"
        role="img"
        aria-label={`${def.label} by minute`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke="#e2e8f0" strokeWidth={1} />
            <text x={M.left - 6} y={y(v)} textAnchor="end" dominantBaseline="central" fontSize={10} fill="#64748b">
              {v}
            </text>
          </g>
        ))}
        {TICKS.map((m) => {
          const s = m <= 45 ? m * 60 : (axis.starts[2] ?? 0) + (m - 45) * 60;
          return (
            <text key={m} x={x(s)} y={H - 6} textAnchor="middle" fontSize={10} fill="#64748b">
              {m}&apos;
            </text>
          );
        })}
        {axis.starts[2] !== undefined && (
          <line x1={x(axis.starts[2])} x2={x(axis.starts[2])} y1={M.top} y2={H - M.bottom} stroke="#cbd5e1" strokeWidth={1} />
        )}
        {metric === "control" &&
          markers
            .filter((m) => m.t <= t)
            .map((m) => (
              <path key={m.label + m.t} d={`M ${x(m.t) - 5} ${M.top - 12} h 10 l -5 7 z`} fill={m.color}>
                <title>{m.label}</title>
              </path>
            ))}
        {visible.map(({ id, color, points, kind }) => {
          if (kind === "dots") {
            return points.map((p, i) => (
              <circle key={`${id}${i}`} cx={x(p.s)} cy={y(p.v)} r={4.5} fill={color} stroke="white" strokeWidth={2} />
            ));
          }
          if (points.length < 2) return null;
          const d =
            kind === "step"
              ? points.map((p, i) => (i === 0 ? `M ${x(p.s)} ${y(p.v)}` : `H ${x(p.s)} V ${y(p.v)}`)).join(" ")
              : points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.s)} ${y(p.v)}`).join(" ");
          return (
            <path key={id} d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          );
        })}
        {visible.map(({ id, color, label, kind }, i) => {
          const last = ends[i];
          if (!last || kind === "dots") return null;
          const other = ends[1 - i];
          const close = other && visible[1 - i]?.kind !== "dots" && Math.abs(y(other.v) - y(last.v)) < 12;
          const nudge = close ? (last.v >= (other?.v ?? 0) ? -7 : 7) : 0;
          return (
            <g key={id}>
              <circle cx={x(last.s)} cy={y(last.v)} r={4} fill={color} stroke="white" strokeWidth={2} />
              <text x={x(last.s) + 8} y={y(last.v) + nudge} dominantBaseline="central" fontSize={11} fontWeight={600} fill="#0f172a">
                {label}
              </text>
            </g>
          );
        })}
        <line x1={x(Math.min(t, axis.total))} x2={x(Math.min(t, axis.total))} y1={M.top} y2={H - M.bottom} stroke="#0f172a" strokeWidth={1} opacity={0.5} />
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} stroke="#64748b" strokeWidth={1} />}
      </svg>
      <p className="mt-auto text-[11px] text-slate-500">{NOTES[metric]}</p>
    </div>
  );
}


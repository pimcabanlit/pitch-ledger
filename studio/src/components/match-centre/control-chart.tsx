"use client";

import { useMemo, useState } from "react";

import { type Axis, toAxis } from "@/components/match-centre/replay";
import type { TeamMinute } from "@/lib/api";
import { minuteLabel } from "@/lib/utils";

export interface Side {
  team_id: string;
  short_name: string;
  color: string;
}

const W = 600;
const H = 220;
const M = { top: 16, right: 44, bottom: 24, left: 30 };
const TICKS = [0, 15, 30, 45, 60, 75, 90];

/**
 * The engine's control score per team, minute by minute, drawn up to the replay clock:
 * one thin line per team, a crosshair readout, and markers where a momentum insight aired.
 */
export function ControlChart({
  axis,
  t,
  rows,
  sides,
  markers,
}: {
  axis: Axis;
  t: number;
  rows: TeamMinute[];
  sides: Side[];
  markers: { t: number; color: string; label: string }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const x = (s: number) => M.left + (s / axis.total) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - v / 100) * (H - M.top - M.bottom);

  const series = useMemo(
    () =>
      sides.map((side) => ({
        side,
        points: rows
          .filter((r) => r.team_id === side.team_id && r.control !== null)
          .map((r) => ({ s: toAxis(axis, r.period, (r.minute - (r.period === 2 ? 45 : 0)) * 60000), minute: r.minute, v: r.control as number })),
      })),
    [rows, sides, axis],
  );
  const visible = series.map((s) => ({ ...s, points: s.points.filter((p) => p.s <= t) }));
  const current = visible.map((s) => s.points.at(-1));
  const readoutAt = hover ?? (current[0]?.s ?? null);
  const readout = visible.map((s) => {
    if (readoutAt === null) return undefined;
    let best = s.points[0];
    for (const p of s.points) if (Math.abs(p.s - readoutAt) < Math.abs((best?.s ?? 0) - readoutAt)) best = p;
    return best;
  });

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const s = (((e.clientX - box.left) / box.width) * W - M.left) / (W - M.left - M.right) * axis.total;
    setHover(s >= 0 && s <= t ? s : null);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-slate-600">
        <span className="font-medium text-slate-900">
          Control score{readout[0] ? ` at ${minuteLabel(readout[0].minute)}` : ""}
        </span>
        {sides.map((side, i) => (
          <span key={side.team_id} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded" style={{ background: side.color }} />
            {side.short_name}
            <strong className="font-mono text-slate-900">{readout[i]?.v ?? "–"}</strong>
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full touch-none"
        role="img"
        aria-label="Control score per team by minute"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {[0, 25, 50, 75, 100].map((v) => (
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
        {markers
          .filter((m) => m.t <= t)
          .map((m) => (
            <path key={m.label + m.t} d={`M ${x(m.t) - 5} ${M.top - 12} h 10 l -5 7 z`} fill={m.color}>
              <title>{m.label}</title>
            </path>
          ))}
        {visible.map(({ side, points }) =>
          points.length > 1 ? (
            <polyline
              key={side.team_id}
              points={points.map((p) => `${x(p.s)},${y(p.v)}`).join(" ")}
              fill="none"
              stroke={side.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ) : null,
        )}
        {visible.map(({ side, points }, i) => {
          const last = points.at(-1);
          if (!last) return null;
          const other = current[1 - i];
          const nudge = other && Math.abs(y(other.v) - y(last.v)) < 12 ? (last.v >= other.v ? -7 : 7) : 0;
          return (
            <g key={side.team_id}>
              <circle cx={x(last.s)} cy={y(last.v)} r={4} fill={side.color} stroke="white" strokeWidth={2} />
              <text x={x(last.s) + 8} y={y(last.v) + nudge} dominantBaseline="central" fontSize={11} fontWeight={600} fill="#0f172a">
                {side.short_name}
              </text>
            </g>
          );
        })}
        <line x1={x(Math.min(t, axis.total))} x2={x(Math.min(t, axis.total))} y1={M.top} y2={H - M.bottom} stroke="#0f172a" strokeWidth={1} opacity={0.5} />
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} stroke="#64748b" strokeWidth={1} />
        )}
      </svg>
      <p className="text-[11px] text-slate-500">
        Rolling five-minute window, computed by the engine (possession, passing and pressing). ▼ marks an
        aired momentum insight.
      </p>
    </div>
  );
}

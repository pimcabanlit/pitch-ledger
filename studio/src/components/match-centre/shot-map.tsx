import type { Side } from "@/components/match-centre/metric-chart";
import { ChartPitch, svgY } from "@/components/match-centre/pitch";
import { type Axis, toAxis } from "@/components/match-centre/replay";
import type { ShotPoint } from "@/lib/api";
import { minuteLabel, matchMinute } from "@/lib/utils";

/**
 * Shots up to the replay clock, each team shown attacking one goal (home to the right):
 * circles for the home side, squares for the away side, area by xG, goals filled.
 */
export function ShotMap({
  axis,
  t,
  shots,
  sides,
  nameOf,
}: {
  axis: Axis;
  t: number;
  shots: ShotPoint[];
  sides: Side[];
  nameOf: (pid: string | null) => string;
}) {
  const [home] = sides;
  const shown = shots.filter((s) => toAxis(axis, s.period, s.clock_ms) <= t);
  const latest = (team: string) => shown.filter((s) => s.team_id === team).at(-1);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-slate-600">
        <span className="font-medium text-slate-900">Shots so far</span>
        {sides.map((side) => {
          const last = latest(side.team_id);
          return (
            <span key={side.team_id} className="flex items-center gap-1.5">
              <svg width={10} height={10} aria-hidden>
                {side.team_id === home?.team_id ? (
                  <circle cx={5} cy={5} r={4} fill={side.color} />
                ) : (
                  <rect x={1} y={1} width={8} height={8} rx={1} fill={side.color} />
                )}
              </svg>
              {side.short_name}
              <strong className="font-mono text-slate-900">{last?.team_shots ?? 0}</strong> shots ·
              <strong className="font-mono text-slate-900">{(last?.team_xg ?? 0).toFixed(2)}</strong> xG
            </span>
          );
        })}
      </div>
      <ChartPitch label="Shot map">
        {shown.map((s) => {
          // Each team attacks one goal on the map: home toward x = 105 (second half mirrored back).
          const flip = s.period === 2;
          const isHome = s.team_id === home?.team_id;
          const x0 = flip ? 105 - s.x : s.x;
          const y0 = flip ? 68 - s.y : s.y;
          const color = sides.find((side) => side.team_id === s.team_id)?.color ?? "#64748b";
          const r = 0.9 + 4.2 * Math.sqrt(s.xg);
          const goal = s.outcome === "goal";
          const title = `${nameOf(s.player_id)} ${minuteLabel(matchMinute(s.period, s.clock_ms))} · xG ${s.xg.toFixed(2)} · ${s.outcome.replaceAll("_", " ")}`;
          const paint = { fill: goal ? color : "white", fillOpacity: goal ? 1 : 0.85, stroke: color, strokeWidth: 0.5 };
          return isHome ? (
            <circle key={s.seq} cx={x0} cy={svgY(y0)} r={r} {...paint}>
              <title>{title}</title>
            </circle>
          ) : (
            <rect key={s.seq} x={x0 - r} y={svgY(y0) - r} width={2 * r} height={2 * r} rx={0.4} {...paint}>
              <title>{title}</title>
            </rect>
          );
        })}
      </ChartPitch>
      <p className="text-[11px] text-slate-500">
        Size by expected goals (xG, computed in code from the shot&apos;s position and type). Filled: goal.
        Hover a shot for details.
      </p>
    </div>
  );
}

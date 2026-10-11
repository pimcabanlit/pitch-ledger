"use client";

import { useEffect, useRef, useState } from "react";

import type { Side } from "@/components/match-centre/metric-chart";
import { ChartPitch, svgY } from "@/components/match-centre/pitch";
import { api, type PassNetwork as Network } from "@/lib/api";

/**
 * One team's completed passes over the ten minutes before the replay clock (same half):
 * players at their average passing position, links weighted by passes between them.
 * Fetched from the API per match minute; the team is drawn attacking to the right.
 */
export function PassNetwork({
  matchId,
  side,
  isHome,
  period,
  clockMs,
  nameOf,
  numberOf,
}: {
  matchId: string;
  side: Side;
  isHome: boolean;
  period: number;
  clockMs: number;
  nameOf: (pid: string | null) => string;
  numberOf: (pid: string) => string;
}) {
  const minute = Math.floor(clockMs / 60000);
  const key = `${side.team_id}:${period}:${minute}`;
  const [net, setNet] = useState<{ key: string; data: Network } | null>(null);
  const latest = useRef("");

  useEffect(() => {
    latest.current = key;
    const timer = setTimeout(() => {
      api.passNetwork(matchId, side.team_id, period, minute * 60000).then((data) => {
        if (latest.current === key) setNet({ key, data });
      });
    }, 250); // scrubbing settles before fetching
    return () => clearTimeout(timer);
  }, [matchId, side.team_id, period, minute, key]);

  const data = net?.data;
  // Broadcast frame -> this team attacking right: away mirrored, both mirrored after half-time.
  const mirror = (isHome ? 0 : 1) ^ (period === 2 ? 1 : 0);
  const at = (x: number, y: number) => (mirror ? { x: 105 - x, y: 68 - y } : { x, y });
  const pos = new Map((data?.nodes ?? []).map((n) => [n.player_id, { ...n, ...at(n.x, n.y) }]));
  const top = Math.max(1, ...(data?.edges ?? []).map((e) => e.passes));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 text-xs text-slate-600">
        <span className="flex items-center gap-1.5 font-medium text-slate-900">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: side.color }} />
          {side.short_name} pass network
        </span>
        {data && (
          <span>
            <strong className="font-mono text-slate-900">{data.passes}</strong> completed passes,{" "}
            {Math.floor(data.from_ms / 60000) + (period === 2 ? 45 : 0)}&apos;–{minute + (period === 2 ? 45 : 0)}&apos;
          </span>
        )}
      </div>
      <ChartPitch label={`${side.short_name} pass network`}>
        {(data?.edges ?? []).map((e) => {
          const a = pos.get(e.a);
          const b = pos.get(e.b);
          if (!a || !b) return null;
          return (
            <line
              key={`${e.a}-${e.b}`}
              x1={a.x}
              y1={svgY(a.y)}
              x2={b.x}
              y2={svgY(b.y)}
              stroke={side.color}
              strokeOpacity={0.25 + 0.6 * (e.passes / top)}
              strokeWidth={0.25 + 1.4 * (e.passes / top)}
              strokeLinecap="round"
            >
              <title>{`${nameOf(e.a)} ↔ ${nameOf(e.b)}: ${e.passes} passes`}</title>
            </line>
          );
        })}
        {[...pos.values()].map((n) => (
          <g key={n.player_id} transform={`translate(${n.x} ${svgY(n.y)})`}>
            <circle r={1.4 + 0.25 * Math.sqrt(n.involvements)} fill={side.color} stroke="white" strokeWidth={0.4}>
              <title>{`${nameOf(n.player_id)}: ${n.involvements} passes made or received`}</title>
            </circle>
            <text textAnchor="middle" dominantBaseline="central" fontSize={1.6} fontWeight={700} fill="white" style={{ pointerEvents: "none" }}>
              {numberOf(n.player_id)}
            </text>
          </g>
        ))}
      </ChartPitch>
      {data && data.passes === 0 && (
        <p className="text-[11px] text-slate-500">No completed passes in this window yet.</p>
      )}
    </div>
  );
}

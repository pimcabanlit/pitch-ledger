"use client";

import { useEffect, useState } from "react";
import { UserRound, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { InfoTip } from "@/components/ui/info-tip";
import { api, type Overlay, type Player, type PlayerMinute, type PlayerTimeline, type Team } from "@/lib/api";
import { matchMinute, minuteLabel } from "@/lib/utils";

const ZERO: Omit<PlayerMinute, "period" | "clock_ms" | "minute"> = {
  passes: 0, passes_completed: 0, pass_accuracy: null, shots: 0, xg: 0, goals: 0,
  distance_km: null, top_speed_kmh: null, sprints: null,
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-slate-50 px-2.5 py-1.5">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="font-mono text-sm font-semibold text-slate-900">{value}</div>
    </div>
  );
}

/**
 * The followed player as of the replay clock: running totals from the API (events and
 * tracking, computed by the engine), whether they are on the pitch, and their aired moments.
 */
export function PlayerCard({
  matchId,
  player,
  team,
  period,
  clockMs,
  onPitch,
  moments,
  onJump,
  onUnfollow,
}: {
  matchId: string;
  player: Player;
  team: Team;
  period: number;
  clockMs: number;
  onPitch: boolean;
  moments: { overlay: Overlay; t: number }[];
  onJump: (t: number) => void;
  onUnfollow: () => void;
}) {
  const key = `${matchId}/${player.player_id}`;
  const [result, setResult] = useState<{ key: string; data: PlayerTimeline | null; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    api.playerTimeline(matchId, player.player_id).then(
      (data) => live && setResult({ key, data }),
      (e: Error) => live && setResult({ key, data: null, error: e.message }),
    );
    return () => {
      live = false;
    };
  }, [matchId, player.player_id, key, attempt]);

  const current = result?.key === key ? result : null;
  const rows = current?.data?.minutes ?? [];
  let row: Omit<PlayerMinute, "period" | "clock_ms" | "minute"> = ZERO;
  for (const r of rows) {
    if (r.period < period || (r.period === period && r.clock_ms <= clockMs)) row = r;
    else break;
  }
  const asOf = rows.length ? minuteLabel(matchMinute(period, clockMs)) : null;

  return (
    <Card className="space-y-3 p-4" aria-label={`Following ${player.display_name}`}>
      <div className="flex items-start gap-3">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-mono text-sm font-bold text-white ring-2 ring-sky-400 ring-offset-2"
          style={{ background: team.primary_color }}
        >
          {player.shirt_number}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 font-medium">
            <UserRound className="h-4 w-4 text-sky-600" aria-hidden />
            <span className="truncate">{player.display_name}</span>
            <InfoTip term="follow" align="end" />
          </div>
          <div className="text-xs text-slate-500">
            {team.name} · {player.position} · {onPitch ? "on the pitch" : "not on the pitch"}
          </div>
        </div>
        <button
          type="button"
          onClick={onUnfollow}
          className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          aria-label={`Stop following ${player.display_name}`}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {current?.error ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-rose-700">
          Could not load their stats: {current.error}
          <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </Button>
        </div>
      ) : !current ? (
        <div className="grid animate-pulse grid-cols-3 gap-2" aria-busy="true">
          <span className="sr-only">Loading their stats…</span>
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="h-11 rounded-md bg-slate-100" />
          ))}
        </div>
      ) : (
        <div>
          <div className="mb-1 text-[11px] text-slate-500">Match totals up to {asOf}</div>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Passes" value={`${row.passes_completed}/${row.passes}`} />
            <Stat label="Pass accuracy" value={row.pass_accuracy !== null ? `${row.pass_accuracy}%` : "–"} />
            <Stat label="Shots · goals" value={`${row.shots} · ${row.goals}`} />
            <Stat label="xG" value={row.xg.toFixed(2)} />
            <Stat label="Distance" value={row.distance_km !== null ? `${row.distance_km.toFixed(1)} km` : "–"} />
            <Stat label="Top speed" value={row.top_speed_kmh !== null ? `${row.top_speed_kmh} km/h` : "–"} />
          </div>
        </div>
      )}

      <div>
        <div className="text-[11px] text-slate-500">Their moments</div>
        {moments.length === 0 ? (
          <p className="text-sm text-slate-500">None aired yet.</p>
        ) : (
          <ol className="mt-1 space-y-1">
            {moments.map(({ overlay, t }) => (
              <li key={overlay.overlay_id}>
                <button
                  type="button"
                  onClick={() => onJump(t)}
                  className="flex w-full gap-2 rounded-md px-1.5 py-1 text-left text-sm hover:bg-sky-50"
                >
                  <span className="w-8 shrink-0 font-mono text-xs text-slate-500">
                    {minuteLabel(matchMinute(overlay.period, overlay.trigger_clock_ms))}
                  </span>
                  <span className="min-w-0 leading-snug">{overlay.text}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}

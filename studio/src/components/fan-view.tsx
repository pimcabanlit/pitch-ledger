"use client";

import { Radio } from "lucide-react";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { api, LANGUAGES, MODES, type MatchDetail, type Mode, type Overlay } from "@/lib/api";
import { useRealtime } from "@/lib/realtime";
import { matchMinute, minuteLabel } from "@/lib/utils";

const MODE_LABELS: Record<Mode, string> = {
  casual: "Casual fan",
  analyst: "Analyst",
  club_focus: "Club focus",
  player_focus: "Player focus",
  single_metric: "Single metric",
};
const POLL_MS = 5000; // without push
const PUSH_POLL_MS = 30000; // safety net while push is connected

export function FanView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [mode, setMode] = useState<Mode>("casual");
  const [language, setLanguage] = useState("en");
  const [club, setClub] = useState("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.match(id).then(setMatch, (e: Error) => setError(e.message));
  }, [id]);

  const load = useCallback(
    () =>
      api.overlays(id, mode, language).then(setOverlays, (e: Error) => setError(e.message)),
    [id, mode, language],
  );
  const pushed = useRealtime(id, false, (signal) => {
    if (signal.event === "overlays_changed") load();
  });

  useEffect(() => {
    load();
    const timer = setInterval(load, pushed ? PUSH_POLL_MS : POLL_MS);
    return () => clearInterval(timer);
  }, [load, pushed]);

  const shown = useMemo(
    () =>
      overlays
        .filter((o) => club === "all" || !o.data.team_id || o.data.team_id === club)
        .slice()
        .reverse(),
    [overlays, club],
  );

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  if (!match) return <p className="text-sm text-slate-500">Loading match…</p>;
  const teams = { [match.home.team_id]: match.home, [match.away.team_id]: match.away };

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-4 text-lg font-semibold">
          <span>{match.home.name}</span>
          <span className="rounded-md bg-slate-900 px-3 py-1 font-mono text-white">
            {match.score_home ?? 0} – {match.score_away ?? 0}
          </span>
          <span>{match.away.name}</span>
        </div>
        <div className="text-sm text-slate-500">
          {match.venue}
          {match.last_minute !== null && ` · ${minuteLabel(match.last_minute)}`}
        </div>
      </Card>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Mode
          <Select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            {MODES.map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Language
          <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Club
          <Select value={club} onChange={(e) => setClub(e.target.value)}>
            <option value="all">Both clubs</option>
            <option value={match.home.team_id}>{match.home.name}</option>
            <option value={match.away.team_id}>{match.away.name}</option>
          </Select>
        </label>
        <span className="ml-auto flex items-center gap-1 text-xs text-emerald-700">
          <Radio className="h-3.5 w-3.5" aria-hidden />
          {pushed ? "live" : `updates every ${POLL_MS / 1000} s`}
        </span>
      </div>

      {shown.length === 0 ? (
        <p className="text-sm text-slate-500">No aired insights yet for this selection.</p>
      ) : (
        <ol className="space-y-2">
          {shown.map((o) => {
            const team = o.data.team_id ? teams[o.data.team_id] : undefined;
            return (
              <li key={o.overlay_id}>
                <Card
                  className="flex gap-3 border-l-4 p-3"
                  style={{ borderLeftColor: team?.primary_color ?? "#94a3b8" }}
                >
                  <span className="w-10 shrink-0 font-mono text-sm text-slate-500">
                    {minuteLabel(matchMinute(o.period, o.trigger_clock_ms))}
                  </span>
                  <div className="space-y-1">
                    <p className="text-sm leading-relaxed">{o.text}</p>
                    <div className="flex gap-2">
                      <Badge>{o.template.replaceAll("_", " ")}</Badge>
                      {team && <Badge>{team.short_name}</Badge>}
                    </div>
                  </div>
                </Card>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

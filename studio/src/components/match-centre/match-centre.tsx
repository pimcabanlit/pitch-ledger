"use client";

import { Pause, Play, Radio, RotateCcw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Caption } from "@/components/match-centre/caption";
import { Feed, type FeedItem } from "@/components/match-centre/feed";
import { Pitch, type Dot } from "@/components/match-centre/pitch";
import {
  axisFromMinute,
  ballAt,
  buildAxis,
  fromAxis,
  lastIndexAt,
  locatedEvents,
  minuteAt,
  toAxis,
  useReplayClock,
  useTracking,
} from "@/components/match-centre/replay";
import { Card, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { api, LANGUAGES, MODES, type MatchDetail, type Mode, type Overlay, type Timeline } from "@/lib/api";
import { useRealtime } from "@/lib/realtime";
import { minuteLabel } from "@/lib/utils";

const MODE_LABELS: Record<Mode, string> = {
  casual: "Casual fan",
  analyst: "Analyst",
  club_focus: "Club focus",
  player_focus: "Player focus",
  single_metric: "Single metric",
};
const SPEEDS = [1, 5, 10, 25, 60];
const MIN_CAPTION_S = 8; // match seconds a caption stays on screen at the slowest speed

export function MatchCentre({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const search = useSearchParams();
  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.match(id).then(setMatch, (e: Error) => setError(e.message));
    api.timeline(id).then(setTimeline, (e: Error) => setError(e.message));
  }, [id]);

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  if (!match || !timeline) return <p className="text-sm text-slate-500">Loading the match…</p>;
  return <Centre match={match} timeline={timeline} startMinute={Number(search.get("t") ?? "") || null} />;
}

function Centre({ match, timeline, startMinute }: { match: MatchDetail; timeline: Timeline; startMinute: number | null }) {
  const axis = useMemo(() => buildAxis(timeline), [timeline]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);
  const [mode, setMode] = useState<Mode>("casual");
  const [language, setLanguage] = useState("en");
  const [club, setClub] = useState("all");
  const [insightsOnly, setInsightsOnly] = useState(false);
  const stop = useCallback(() => setPlaying(false), []);
  const [t, setT] = useReplayClock(axis.total, speed, playing, stop);

  const started = useRef(false);
  useEffect(() => {
    if (started.current || startMinute === null) return;
    started.current = true;
    setT(axisFromMinute(axis, startMinute));
  }, [axis, startMinute, setT]);

  // Aired overlays in the viewer's mode and language (a slow reply for a left selection is ignored).
  const selection = `${mode}/${language}`;
  const [result, setResult] = useState<{ key: string; rows: Overlay[] } | null>(null);
  const latest = useRef("");
  const loadOverlays = useCallback(() => {
    latest.current = selection;
    api.overlays(match.match_id, mode, language).then((rows) => {
      if (latest.current === selection) setResult({ key: selection, rows });
    });
  }, [match.match_id, mode, language, selection]);
  useEffect(loadOverlays, [loadOverlays]);
  const pushed = useRealtime(match.match_id, false, (signal) => {
    if (signal.event === "overlays_changed") loadOverlays();
  });

  const teams = useMemo(
    () => ({ [match.home.team_id]: match.home, [match.away.team_id]: match.away }),
    [match.home, match.away],
  );
  const players = useMemo(() => new Map(match.players.map((p) => [p.player_id, p])), [match.players]);
  const nameOf = useCallback((pid: string | null) => (pid ? (players.get(pid)?.display_name ?? pid) : ""), [players]);
  const colorOf = useCallback(
    (team: string | null | undefined) => (team ? (teams[team]?.primary_color ?? "#64748b") : "#64748b"),
    [teams],
  );
  const keep = useCallback((team: string | null | undefined) => club === "all" || !team || team === club, [club]);

  const times = useMemo(() => timeline.events.map((e) => toAxis(axis, e.period, e.clock_ms)), [timeline, axis]);
  const located = useMemo(() => locatedEvents(axis, timeline.events), [timeline, axis]);
  const insights = useMemo(
    () =>
      (result?.key === selection ? result.rows : [])
        .map((o) => ({ overlay: o, t: toAxis(axis, o.period, o.trigger_clock_ms) }))
        .filter((i) => keep(i.overlay.data.team_id)),
    [result, selection, axis, keep],
  );

  // Where the clock is.
  const { period, clockMs } = fromAxis(axis, t);
  const second = clockMs / 1000;
  const positions = useTracking(match.match_id, period, second)(period, second);
  const index = lastIndexAt(times, t);
  const now = index >= 0 ? timeline.events[index] : null;
  const ended = t >= axis.total - 0.5;

  // The caption on screen: the latest insight the clock has passed, for its display time.
  const window = Math.max(MIN_CAPTION_S, speed * 10);
  const caption = [...insights].reverse().find((i) => i.t <= t + 0.5 && t - i.t <= window)?.overlay ?? null;

  const dots: Dot[] = [];
  positions.forEach((pos, pid) => {
    const p = players.get(pid);
    dots.push({ id: pid, ...pos, color: colorOf(p?.team_id), label: String(p?.shirt_number ?? ""),
                highlight: caption?.data.player_id === pid });  // prettier-ignore
  });

  const tick = Math.floor(t); // the feed changes at most once per match second
  const feed = useMemo(() => {
    const items: FeedItem[] = [];
    if (!insightsOnly) {
      timeline.events.forEach((e, i) => {
        if (!e.key || times[i] > tick || !keep(e.team_id)) return;
        const minute = (e.period === 2 ? 45 : 0) + e.clock_ms / 60000;
        const team = e.team_id ? teams[e.team_id]?.short_name : "";
        const base = { id: `e${e.seq}`, t: times[i], order: e.period * 1e8 + e.clock_ms * 10, minute, color: colorOf(e.team_id) };
        if (e.type === "shot" && e.outcome === "goal") items.push({ ...base, kind: "goal", title: `Goal! ${nameOf(e.player_id)} (${team})` });
        else if (e.type === "shot") items.push({ ...base, kind: "shot", title: `Shot by ${nameOf(e.player_id)} (${team}): ${e.outcome?.replaceAll("_", " ")}` });
        else if (e.type === "card") items.push({ ...base, kind: "card", title: `${e.outcome === "red" ? "Red" : e.outcome === "second_yellow" ? "Second yellow" : "Yellow"} card: ${nameOf(e.player_id)} (${team})` });
        else if (e.type === "substitution") items.push({ ...base, kind: "substitution", title: `Substitution (${team}): ${nameOf(e.player_in_id)} on, ${nameOf(e.player_id)} off` });
        else if (e.type === "period_start") items.push({ ...base, kind: "period", title: e.period === 1 ? "Kick-off" : "Second half under way" });
        else if (e.type === "period_end") items.push({ ...base, kind: "period", title: e.period === 1 ? "Half-time" : "Full-time" });
      });
    }
    for (const { overlay, t: at } of insights) {
      if (at > tick) continue;
      items.push({
        id: overlay.overlay_id, t: at, order: overlay.period * 1e8 + overlay.trigger_clock_ms * 10 + 5,
        minute: (overlay.period === 2 ? 45 : 0) + overlay.trigger_clock_ms / 60000,
        kind: "insight", title: overlay.template.replaceAll("_", " "), text: overlay.text,
        color: colorOf(overlay.data.team_id as string | null), insightId: overlay.insight_id,
      });  // prettier-ignore
    }
    return items.sort((a, b) => b.order - a.order); // newest first; the second half follows half-time
  }, [tick, insightsOnly, insights, timeline, times, keep, nameOf, colorOf, teams]);

  const moments = insights.filter((i) => i.overlay.template !== "speed_badge");
  const jump = (to: number) => setT(Math.max(0, Math.min(axis.total, to)));
  const subject = (o: Overlay) =>
    o.data.player_id ? nameOf(o.data.player_id as string) : teams[o.data.team_id as string]?.name ?? "";

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4">
        <div className="flex items-center gap-3 text-base font-semibold sm:text-lg">
          <span className="h-3 w-3 rounded-full" style={{ background: match.home.primary_color }} />
          <span>{match.home.name}</span>
          <span className="shrink-0 rounded-md bg-slate-900 px-3 py-1 font-mono whitespace-nowrap text-white">
            {now?.score_home ?? 0} – {now?.score_away ?? 0}
          </span>
          <span>{match.away.name}</span>
          <span className="h-3 w-3 rounded-full" style={{ background: match.away.primary_color }} />
        </div>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-2xl font-semibold">{minuteLabel(minuteAt(axis, t))}</span>
          <span className="text-xs text-slate-500">
            {ended ? "Full-time" : period === 1 ? "1st half" : "2nd half"}
          </span>
        </div>
        <span className="ml-auto text-sm text-slate-500">{match.venue}</span>
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Pitch dots={dots} ball={ballAt(located, t)} label={`${match.home.name} v ${match.away.name}, replay`}>
            {caption && <Caption overlay={caption} subject={subject(caption)} color={colorOf(caption.data.team_id as string | null)} />}
          </Pitch>
          {caption && (
            <Caption inline overlay={caption} subject={subject(caption)} color={colorOf(caption.data.team_id as string | null)} />
          )}

          <Card className="space-y-3 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => (ended ? (jump(0), setPlaying(true)) : setPlaying(!playing))}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-slate-900 text-white hover:bg-slate-700"
                aria-label={playing ? "Pause" : "Play"}
              >
                {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              </button>
              <button
                onClick={() => (jump(0), setPlaying(false))}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-300 hover:bg-slate-50"
                aria-label="Back to kick-off"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
              <div className="flex gap-1" role="group" aria-label="Speed">
                {SPEEDS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setSpeed(s)}
                    aria-pressed={speed === s}
                    className={`rounded-md px-2 py-1 text-xs ${speed === s ? "bg-slate-900 text-white" : "border border-slate-300"}`}
                  >
                    {s}×
                  </button>
                ))}
              </div>
              <span className="ml-auto flex items-center gap-1 text-xs text-emerald-700">
                <Radio className="h-3.5 w-3.5" aria-hidden />
                {pushed ? "live" : "replay"}
              </span>
            </div>
            <div className="relative pt-2">
              <div className="pointer-events-none absolute inset-x-0 top-0 h-2">
                {moments.map((m) => (
                  <span
                    key={m.overlay.overlay_id}
                    className="absolute top-0 h-2 w-1 -translate-x-1/2 rounded-full"
                    style={{ left: `${(m.t / axis.total) * 100}%`, background: colorOf(m.overlay.data.team_id as string | null) }}
                  />
                ))}
              </div>
              <input
                type="range"
                min={0}
                max={Math.round(axis.total)}
                step={1}
                value={Math.round(t)}
                onChange={(e) => jump(Number(e.target.value))}
                className="w-full accent-slate-900"
                aria-label="Match clock"
              />
            </div>
            {moments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                <span className="text-xs text-slate-500">Key moments:</span>
                {moments.map((m) => (
                  <button
                    key={m.overlay.overlay_id}
                    onClick={() => jump(m.t)}
                    className="rounded-full border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                  >
                    {minuteLabel((m.overlay.period === 2 ? 45 : 0) + m.overlay.trigger_clock_ms / 60000)}{" "}
                    {m.overlay.template.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>

        <Card aria-label="Match feed">
          <CardHeader className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <Select value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-label="Mode">
                {MODES.map((m) => (
                  <option key={m} value={m}>
                    {MODE_LABELS[m]}
                  </option>
                ))}
              </Select>
              <Select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Language">
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </Select>
              <Select value={club} onChange={(e) => setClub(e.target.value)} aria-label="Club">
                <option value="all">Both clubs</option>
                <option value={match.home.team_id}>{match.home.name}</option>
                <option value={match.away.team_id}>{match.away.name}</option>
              </Select>
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" checked={insightsOnly} onChange={(e) => setInsightsOnly(e.target.checked)} />
              Insights only
            </label>
          </CardHeader>
          <div className="max-h-[36rem] overflow-auto">
            <Feed items={feed} onJump={jump} />
          </div>
        </Card>
      </div>
    </div>
  );
}

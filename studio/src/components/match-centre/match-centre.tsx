"use client";

import { Pause, Play, Radio, RotateCcw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ballAt, ballPath } from "@/components/match-centre/ball";
import { Caption } from "@/components/match-centre/caption";
import { Feed, type FeedItem } from "@/components/match-centre/feed";
import { MetricChart, type InsightPoint, type Side } from "@/components/match-centre/metric-chart";
import { PassNetwork } from "@/components/match-centre/pass-network";
import { Pitch, type Dot } from "@/components/match-centre/pitch";
import { PlayerCard } from "@/components/match-centre/player-card";
import { ScoreBug } from "@/components/match-centre/score-bug";
import {
  axisFromMinute,
  buildAxis,
  fromAxis,
  lastIndexAt,
  minuteAt,
  toAxis,
  useReplayClock,
  useTracking,
} from "@/components/match-centre/replay";
import { ShotMap } from "@/components/match-centre/shot-map";
import { StoryPanel } from "@/components/insight-story";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { InfoTip } from "@/components/ui/info-tip";
import { Select } from "@/components/ui/select";
import {
  api,
  LANGUAGES,
  MODES,
  type Analytics,
  type MatchDetail,
  type Mode,
  type Overlay,
  type Timeline,
} from "@/lib/api";
import { METRIC_KEYS, METRICS, type MetricKey } from "@/lib/metrics";
import { usePreferences } from "@/lib/preferences";
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
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    api.match(id).then(setMatch, (e: Error) => setError(e.message));
    api.timeline(id).then(setTimeline, (e: Error) => setError(e.message));
  }, [id, attempt]);
  useEffect(() => {
    api.analytics(id).then(setAnalytics, (e: Error) => setAnalyticsError(e.message)); // charts are optional
  }, [id, attempt]);

  if (error) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-rose-700">
        Could not load the match: {error}
        <Button variant="outline" size="sm" onClick={() => (setError(null), setAttempt((n) => n + 1))}>
          Try again
        </Button>
      </div>
    );
  }
  if (!match || !timeline) return <MatchSkeleton />;
  return (
    <Centre
      match={match}
      timeline={timeline}
      analytics={analytics}
      analyticsError={analyticsError}
      onRetryAnalytics={() => (setAnalyticsError(null), setAttempt((n) => n + 1))}
      startMinute={Number(search.get("t") ?? "") || null}
      startStory={search.get("story")}
    />
  );
}

export function MatchSkeleton() {
  return (
    <div className="animate-pulse space-y-4" aria-busy="true">
      <span className="sr-only">Loading the match…</span>
      <div className="h-16 rounded-lg bg-slate-200/70" />
      <div className="h-24 rounded-lg bg-slate-200/70" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="aspect-[111/74] rounded-lg bg-slate-200/70" />
        <div className="h-64 rounded-lg bg-slate-200/70" />
      </div>
    </div>
  );
}

/** Scroll an element to the top of the view, smoothly unless the reader prefers less motion. */
function scrollTo(el: HTMLElement | null) {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
}

function Field({ label, children, tip }: { label: string; children: React.ReactNode; tip?: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-slate-600">
      <span className="flex items-center gap-1">
        {label}
        {tip}
      </span>
      {children}
    </label>
  );
}

function Centre({
  match,
  timeline,
  analytics,
  analyticsError,
  onRetryAnalytics,
  startMinute,
  startStory,
}: {
  match: MatchDetail;
  timeline: Timeline;
  analytics: Analytics | null;
  analyticsError: string | null;
  onRetryAnalytics: () => void;
  startMinute: number | null;
  startStory: string | null;
}) {
  const axis = useMemo(() => buildAxis(timeline), [timeline]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);
  const [prefs, setPrefs] = usePreferences();
  const { mode, language, metric } = prefs;
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

  // "Tell me more": the story opens under the replay controls, above the charts, and the page
  // scrolls to it each time one is picked (from the feed, the caption or a key moment).
  const [story, setStory] = useState<{ id: string; asked: number } | null>(() =>
    startStory ? { id: startStory, asked: 0 } : null,
  );
  const storyRef = useRef<HTMLDivElement>(null);
  const pitchRef = useRef<HTMLDivElement>(null);
  const openStory = useCallback((id: string) => setStory((s) => ({ id, asked: (s?.asked ?? 0) + 1 })), []);
  useEffect(() => {
    if (story) scrollTo(storyRef.current);
  }, [story]);

  // Aired overlays in the viewer's mode and language (a slow reply for a left selection is ignored).
  const selection = `${mode}/${language}`;
  const [result, setResult] = useState<{ key: string; rows: Overlay[]; error?: string } | null>(null);
  const latest = useRef("");
  const loadOverlays = useCallback(() => {
    latest.current = selection;
    api.overlays(match.match_id, mode, language).then(
      (rows) => latest.current === selection && setResult({ key: selection, rows }),
      (e: Error) => latest.current === selection && setResult({ key: selection, rows: [], error: e.message }),
    );
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
  const followed = prefs.player && players.has(prefs.player) ? players.get(prefs.player)! : null;
  const favourite = (team: string) => prefs.clubs.includes(team);
  const keep = useCallback((team: string | null | undefined) => club === "all" || !team || team === club, [club]);

  const times = useMemo(() => timeline.events.map((e) => toAxis(axis, e.period, e.clock_ms)), [timeline, axis]);
  const path = useMemo(() => ballPath(axis, timeline.events, match.home.team_id), [timeline, axis, match.home.team_id]);
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
  const trackAt = useTracking(match.match_id, period, second);
  const positions = trackAt(period, second);
  const ball = ballAt(path, t, (pid, at) => {
    const where = fromAxis(axis, at);
    return where.period === period ? trackAt(period, where.clockMs / 1000).get(pid) : undefined;
  });
  const index = lastIndexAt(times, t);
  const now = index >= 0 ? timeline.events[index] : null;
  const ended = t >= axis.total - 0.5;

  // The caption on screen: of the insights the clock has passed that are still showing, the most
  // important (lowest priority number), the latest among equals, so a badge never cuts a goal short.
  const window = Math.max(MIN_CAPTION_S, speed * 10);
  const showing = insights.filter((i) => i.t <= t + 0.5 && t - i.t <= window);
  const top = (a: (typeof showing)[number], b: (typeof showing)[number]) =>
    b.overlay.priority < a.overlay.priority || (b.overlay.priority === a.overlay.priority && b.t >= a.t) ? b : a;
  const caption = showing.length ? showing.reduce(top).overlay : null;

  const dots: Dot[] = [];
  positions.forEach((pos, pid) => {
    const p = players.get(pid);
    const captioned = caption?.data.player_id === pid;
    dots.push({ id: pid, ...pos, color: colorOf(p?.team_id), label: String(p?.shirt_number ?? ""),
                highlight: captioned, followed: followed?.player_id === pid,
                name: captioned ? p?.display_name : undefined });  // prettier-ignore
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
        mine: !!followed && overlay.data.player_id === followed.player_id,
        id: overlay.overlay_id, t: at, order: overlay.period * 1e8 + overlay.trigger_clock_ms * 10 + 5,
        minute: (overlay.period === 2 ? 45 : 0) + overlay.trigger_clock_ms / 60000,
        kind: "insight", title: overlay.template.replaceAll("_", " "), text: overlay.text,
        color: colorOf(overlay.data.team_id as string | null), insightId: overlay.insight_id,
      });  // prettier-ignore
    }
    return items.sort((a, b) => b.order - a.order); // newest first; the second half follows half-time
  }, [tick, insightsOnly, insights, timeline, times, keep, nameOf, colorOf, teams, followed]);

  const moments = insights.filter((i) => !i.overlay.template.endsWith("_badge")); // badges: captions only
  const sides: Side[] = [match.home, match.away].map((team) => ({
    team_id: team.team_id,
    short_name: team.short_name,
    color: team.primary_color,
  }));
  const markers = insights
    .filter((i) => i.overlay.template === "momentum_bar")
    .map((i) => ({ t: i.t, color: colorOf(i.overlay.data.team_id as string | null), label: i.overlay.text }));
  const insightPoints: InsightPoint[] = insights.flatMap(({ overlay, t: at }) =>
    Object.entries((overlay.data.metrics ?? {}) as Record<string, number>)
      .filter(([k]) => METRICS.speed.carries(k))
      .map(([k, v]) => ({ t: at, team_id: (overlay.data.team_id as string | null) ?? null, v, label: k })),
  );
  const theirMoments = followed
    ? insights.filter((i) => i.t <= tick && i.overlay.data.player_id === followed.player_id).reverse()
    : [];
  const metricSelect = (id: string) => (
    <Select id={id} aria-label="Chart" value={metric} onChange={(e) => setPrefs({ metric: e.target.value as MetricKey })}>
      {METRIC_KEYS.map((k) => (
        <option key={k} value={k}>
          {METRICS[k].label}
        </option>
      ))}
    </Select>
  );
  const numberOf = (pid: string) => String(players.get(pid)?.shirt_number ?? "");
  const jump = (to: number) => setT(Math.max(0, Math.min(axis.total, to)));
  const subject = (o: Overlay) =>
    o.data.player_id ? nameOf(o.data.player_id as string) : teams[o.data.team_id as string]?.name ?? "";

  return (
    <div className="space-y-4">
      <h1 className="sr-only">
        {match.home.name} v {match.away.name}
      </h1>

      <Card className="p-3 md:p-4" aria-labelledby="your-view">
        <h2 id="your-view" className="sr-only">
          Your view (remembered on this device)
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="Mode" tip={<InfoTip term="modes" align="start" />}>
            <Select value={mode} onChange={(e) => setPrefs({ mode: e.target.value as Mode })}>
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Language">
            <Select value={language} onChange={(e) => setPrefs({ language: e.target.value })}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Show">
            <Select value={club} onChange={(e) => setClub(e.target.value)}>
              <option value="all">Both clubs</option>
              {[match.home, match.away].map((team) => (
                <option key={team.team_id} value={team.team_id}>
                  {favourite(team.team_id) ? "★ " : ""}
                  {team.name} only
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Follow a player" tip={<InfoTip term="follow" />}>
            <Select
              value={followed?.player_id ?? ""}
              onChange={(e) => setPrefs({ player: e.target.value || null })}
            >
              <option value="">Nobody</option>
              {[match.home, match.away].map((team) => (
                <optgroup key={team.team_id} label={team.name}>
                  {match.players
                    .filter((pl) => pl.team_id === team.team_id)
                    .sort((a, b) => a.shirt_number - b.shirt_number)
                    .map((pl) => (
                      <option key={pl.player_id} value={pl.player_id}>
                        {pl.shirt_number} {pl.display_name}
                      </option>
                    ))}
                </optgroup>
              ))}
            </Select>
          </Field>
          <Field label="Your metric" tip={<InfoTip term={METRICS[metric].term} align="end" />}>
            {metricSelect("metric-view")}
          </Field>
        </div>
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <div ref={pitchRef} className="scroll-mt-20">
            <Pitch dots={dots} ball={ball} label={`${match.home.name} v ${match.away.name}, replay`}>
              {caption && <Caption overlay={caption} subject={subject(caption)} color={colorOf(caption.data.team_id as string | null)} onStory={openStory} />}
              <ScoreBug
                home={match.home}
                away={match.away}
                scoreHome={now?.score_home ?? 0}
                scoreAway={now?.score_away ?? 0}
                minute={minuteLabel(minuteAt(axis, t))}
                half={ended ? "Full-time" : period === 1 ? "1st half" : "2nd half"}
              />
            </Pitch>
          </div>
          {caption && (
            <Caption inline overlay={caption} subject={subject(caption)} color={colorOf(caption.data.team_id as string | null)} onStory={openStory} />
          )}
          <p className="sr-only" aria-live="polite">
            {caption ? `${subject(caption)}: ${caption.text}` : ""}
          </p>

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
                    onClick={() => (jump(m.t), openStory(m.overlay.insight_id))}
                    className="rounded-full border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                  >
                    {minuteLabel((m.overlay.period === 2 ? 45 : 0) + m.overlay.trigger_clock_ms / 60000)}{" "}
                    {m.overlay.template.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
            )}
          </Card>
          {story && (
            <Card ref={storyRef} className="scroll-mt-20" aria-label="Tell me more">
              <StoryPanel
                insightId={story.id}
                onWatch={(minute) => (jump(axisFromMinute(axis, minute)), scrollTo(pitchRef.current))}
                onClose={() => setStory(null)}
              />
            </Card>
          )}
        </div>

        <div className="space-y-4">
          {followed && (
            <PlayerCard
              matchId={match.match_id}
              player={followed}
              team={teams[followed.team_id]}
              period={period}
              clockMs={clockMs}
              onPitch={positions.has(followed.player_id)}
              moments={theirMoments}
              onJump={jump}
              onUnfollow={() => setPrefs({ player: null })}
            />
          )}
          <Card aria-label="Match feed">
            <CardHeader className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h2 className="text-sm font-semibold">Match feed</h2>
              <InfoTip term="statuses" />
              <label className="ml-auto flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" checked={insightsOnly} onChange={(e) => setInsightsOnly(e.target.checked)} />
                Insights only
              </label>
              {result?.key === selection && result.error && (
                <div role="alert" className="flex w-full flex-wrap items-center gap-2 text-xs text-rose-700">
                  Could not load insights: {result.error}
                  <Button variant="outline" size="sm" onClick={loadOverlays}>
                    Try again
                  </Button>
                </div>
              )}
            </CardHeader>
            <div className="max-h-[36rem] overflow-auto">
              <Feed items={feed} onJump={jump} onStory={openStory} />
            </div>
          </Card>
        </div>
      </div>

      {!analytics && analyticsError && (
        <Card role="alert" className="flex flex-wrap items-center gap-3 p-4 text-sm text-rose-700">
          Could not load the charts: {analyticsError}
          <Button variant="outline" size="sm" onClick={onRetryAnalytics}>
            Try again
          </Button>
        </Card>
      )}

      {analytics && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="p-4">
            <MetricChart
              picker={metricSelect("metric-chart")}
              axis={axis}
              t={t}
              metric={metric}
              analytics={analytics}
              sides={sides}
              markers={markers}
              insightPoints={insightPoints}
            />
          </Card>
          <Card className="p-4">
            <ShotMap axis={axis} t={t} shots={analytics.shots} sides={sides} nameOf={nameOf} />
          </Card>
          {sides.map((side, i) => (
            <Card key={side.team_id} className="p-4">
              <PassNetwork
                matchId={match.match_id}
                side={side}
                isHome={i === 0}
                period={period}
                clockMs={clockMs}
                nameOf={nameOf}
                numberOf={numberOf}
              />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

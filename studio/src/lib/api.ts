// Types mirror the API's response schemas (src/pitch_ledger/api/schemas.py).

export type InsightStatus = "aired" | "pending" | "blocked";
export const MODES = ["casual", "analyst", "club_focus", "player_focus", "single_metric"] as const;
export type Mode = (typeof MODES)[number];
export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
  { code: "fil", label: "Filipino" },
] as const;

export interface Team {
  team_id: string;
  name: string;
  short_name: string;
  primary_color: string;
}

export interface MatchSummary {
  match_id: string;
  kickoff_utc: string;
  venue: string;
  home: Team;
  away: Team;
  score_home: number | null; // after the last event: the final score, or the score so far
  score_away: number | null;
  aired_insights: number | null; // match list only
}

export interface Player {
  player_id: string;
  team_id: string;
  display_name: string;
  shirt_number: number;
  position: string;
}

export interface MatchDetail extends MatchSummary {
  players: Player[];
  last_minute: number | null;
}

export interface Overlay {
  overlay_id: string;
  insight_id: string;
  period: number;
  trigger_clock_ms: number;
  display_ms: number;
  priority: number;
  template: string;
  audience_mode: Mode;
  language: string;
  text: string;
  data: { team_id?: string | null; player_id?: string | null; [key: string]: unknown };
}

export interface Insight {
  insight_id: string;
  match_id: string;
  period: number;
  clock_ms: number;
  insight_type: string;
  subject: { team_id: string | null; player_id: string | null };
  headline: string;
  explanation: string;
  metrics: Record<string, number>;
  evidence_event_ids: string[];
  confidence: number;
  audit_status: string;
  audit_notes: string | null;
  approved_by: string | null;
  created_by_agent: string;
  model_version: string;
  trigger_id: string | null;
}

export interface InsightView {
  insight: Insight;
  status: InsightStatus;
}

export interface AuditRecord {
  audit_id: string;
  round: number;
  actor: string;
  action: string;
  passed: boolean | null;
  findings: string[];
  headline: string | null;
  explanation: string | null;
}

export interface EvidenceEvent {
  event_id: string;
  minute: number;
  event_type: string;
  team_id: string | null;
  player_id: string | null;
  outcome: string | null;
}

export interface InsightDetail extends InsightView {
  audits: AuditRecord[];
  evidence: EvidenceEvent[];
  overlays: Overlay[];
}

export interface DecisionResult {
  insight_id: string;
  status: InsightStatus;
  audit_status: string | null;
  overlays: number;
  approved_by: string | null;
}

export type StageKey = "detected" | "analyzed" | "explained" | "audited" | "approved" | "published";

export interface StoryStage {
  key: StageKey;
  label: string;
  done: boolean;
  detail: string;
  at: string | null;
}

export interface StoryStep {
  audit_id: string;
  round: number;
  agent: string;
  role: "agent" | "test" | "producer";
  action: string;
  passed: boolean | null;
  findings: string[];
  headline: string | null;
  explanation: string | null;
  unsupported_numbers: string[];
  recorded_at: string | null;
}

export interface StoryWindow {
  label: string;
  start_minute: number;
  end_minute: number;
}

export interface InsightStory {
  match: MatchSummary;
  insight: Insight;
  status: InsightStatus;
  minute: number;
  trigger: {
    trigger_id: string;
    rule_id: string;
    minute: number;
    detector: string | null;
    metrics: Record<string, number>;
    windows: StoryWindow[];
    season_matches: string[]; // earlier matches this season behind any season facts
  } | null;
  stages: StoryStage[];
  steps: StoryStep[];
  evidence: EvidenceEvent[];
  evidence_counts: Record<string, number>;
  overlays: Overlay[];
}

export interface TimelineEvent {
  seq: number;
  period: number;
  clock_ms: number;
  type: string;
  outcome: string | null;
  team_id: string | null;
  player_id: string | null;
  receiver_id: string | null;
  x: number | null; // broadcast frame: home attacks toward x = 105 in period 1
  y: number | null;
  end_x: number | null;
  end_y: number | null;
  duration_ms: number | null; // ball travel time (passes, shots) or run time (carries)
  score_home: number;
  score_away: number;
  key: boolean;
  player_in_id: string | null;
}

export interface Timeline {
  match_id: string;
  home_team_id: string;
  away_team_id: string;
  periods: { period: number; end_ms: number }[];
  events: TimelineEvent[];
}

export interface TrackingWindow {
  period: number;
  from_s: number;
  to_s: number;
  players: string[];
  frames: (number | null)[][];
}

export interface TeamMinute {
  team_id: string;
  period: number;
  minute: number;
  control: number | null; // 0-100 per team
  possession_pct: number | null;
  ppda: number | null;
  pass_accuracy: number | null; // % of passes completed
  tempo: number | null; // passes per minute on the ball
}

export interface ChaosMinute {
  period: number;
  minute: number;
  chaos: number; // 0-100, the whole match (not per team)
}

export interface ShotPoint {
  seq: number;
  period: number;
  clock_ms: number;
  team_id: string;
  player_id: string | null;
  x: number; // broadcast frame
  y: number;
  xg: number;
  outcome: string;
  team_shots: number; // the team's shots so far, this one included
  team_xg: number;
}

export interface Analytics {
  team_state: TeamMinute[];
  chaos: ChaosMinute[];
  shots: ShotPoint[];
}

/** A player's whole-match totals up to clock_ms of period (included). */
export interface PlayerMinute {
  period: number;
  clock_ms: number;
  minute: number;
  passes: number;
  passes_completed: number;
  pass_accuracy: number | null;
  shots: number;
  xg: number;
  goals: number;
  distance_km: number | null; // null until the player has been tracked (an unused substitute)
  top_speed_kmh: number | null;
  sprints: number | null;
}

export interface PlayerTimeline {
  player_id: string;
  team_id: string;
  minutes: PlayerMinute[];
}

export interface PassNetwork {
  team_id: string;
  period: number;
  from_ms: number;
  to_ms: number;
  passes: number;
  nodes: { player_id: string; x: number; y: number; involvements: number }[];
  edges: { a: string; b: string; passes: number }[];
}

export interface Realtime {
  url: string | null; // null: push is off, poll instead
  group: string;
}

export type AlertSeverity = "info" | "warning" | "critical";

export interface OpsAlert {
  alert_id: string;
  match_id: string;
  kind: string;
  severity: AlertSeverity;
  message: string;
  refs: string[];
  raised_at: string;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface PushSignal {
  event: "overlays_changed" | "insight_changed" | "alert_changed" | string;
  match_id: string;
  insight_id?: string;
  status?: InsightStatus;
  alert_id?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let tokenProvider: (() => Promise<string | null>) | null = null;

/** Producer sign-in registers how to get an access token for the API (null: signed out). */
export function setTokenProvider(provider: (() => Promise<string | null>) | null) {
  tokenProvider = provider;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = tokenProvider ? await tokenProvider() : null;
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  matches: () => request<MatchSummary[]>("/matches"),
  match: (id: string) => request<MatchDetail>(`/matches/${id}`),
  overlays: (id: string, mode: Mode, language: string) =>
    request<Overlay[]>(`/matches/${id}/overlays?mode=${mode}&language=${language}`),
  insights: (id: string) => request<InsightView[]>(`/matches/${id}/insights`),
  insight: (id: string) => request<InsightDetail>(`/insights/${id}`),
  story: (insightId: string) => request<InsightStory>(`/insights/${insightId}/story`),
  timeline: (matchId: string) => request<Timeline>(`/matches/${matchId}/timeline`),
  analytics: (matchId: string) => request<Analytics>(`/matches/${matchId}/analytics`),
  playerTimeline: (matchId: string, playerId: string) =>
    request<PlayerTimeline>(`/matches/${matchId}/players/${playerId}/timeline`),
  passNetwork: (matchId: string, team: string, period: number, endMs: number) =>
    request<PassNetwork>(`/matches/${matchId}/pass-network?team=${team}&period=${period}&end_ms=${endMs}`),
  tracking: (matchId: string, period: number, fromS: number, toS: number) =>
    request<TrackingWindow>(`/matches/${matchId}/tracking?period=${period}&from_s=${fromS}&to_s=${toS}`),
  /** The broadcast package as JSON text (shown and downloaded as is). */
  broadcast: async (insightId: string) => {
    const response = await fetch(`/api/v1/insights/${insightId}/broadcast`, { cache: "no-store" });
    if (!response.ok) throw new ApiError(response.status, response.statusText);
    return JSON.stringify(await response.json(), null, 2);
  },
  negotiate: (matchId: string, producer = false) =>
    request<Realtime>(`/realtime/negotiate${producer ? "/producer" : ""}?match_id=${matchId}`),
  alerts: (matchId: string) => request<OpsAlert[]>(`/matches/${matchId}/alerts`),
  acknowledge: (alertId: string) =>
    request<OpsAlert>(`/alerts/${alertId}/acknowledge`, { method: "POST" }),
  decide: (id: string, approve: boolean, reason?: string) =>
    request<DecisionResult>(`/insights/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(reason ? { approve, reason } : { approve }),
    }),
};

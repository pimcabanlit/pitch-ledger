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
  score_home: number | null;
  score_away: number | null;
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

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
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
  decide: (id: string, approve: boolean, reason?: string) =>
    request<DecisionResult>(`/insights/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(reason ? { approve, reason } : { approve }),
    }),
};
